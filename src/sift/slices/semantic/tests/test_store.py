# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keeping the numbers that describe pictures, and finding the nearest ones.

An install without the add-on loses only this feature; describing a file again replaces what was
held; a page of results is a page of files, not frames.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from sift.kernel.db import Database, StatementRun, statement_budget
from sift.kernel.forgetting import forget_everywhere
from sift.slices.semantic.store import (
    _CREATE,
    _CREATE_FILES,
    DIMENSION,
    EXTENSION,
    K_LIMIT,
    VectorStore,
    VectorStoreUnavailable,
    _pack,
)

pytestmark = pytest.mark.integration

#: The model every description in these tests comes from, unless a test says otherwise.
REVISION = "siglip2-test"


def vector(*leading: float) -> list[float]:
    """A vector of the right width, with the interesting numbers at the front."""
    rest = [0.0] * (DIMENSION - len(leading))
    return [*leading, *rest]


def unit(*leading: float) -> list[float]:
    """The same, scaled to length one, so distances between them are comparable."""
    values = vector(*leading)
    length = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / length for value in values]


@pytest.fixture
async def store(temp_db: Database) -> VectorStore:
    # The pooled description is an ordinary table made at boot; the frames are made lazily.
    await temp_db.initialize_schema()
    return VectorStore(temp_db)


# --- a machine that cannot load the add-on --------------------------------------------------


@pytest.fixture
def without_extension(temp_db: Database) -> VectorStore:
    """A database that opened without the add-on, which is a real machine and not a contrivance:
    SQLite can be built without the ability to load one at all."""
    temp_db._extensions = frozenset()
    return VectorStore(temp_db)


def test_the_add_on_loads_on_this_machine(temp_db: Database) -> None:
    """The rest of this file is only meaningful if it did."""
    assert EXTENSION in temp_db.extensions


def test_the_width_in_the_table_is_the_width_the_code_believes_in() -> None:
    """The table's width is the width the code expects, or every write is refused."""
    assert f"float[{DIMENSION}]" in _CREATE
    assert f"float[{DIMENSION}]" in _CREATE_FILES


def test_a_machine_without_the_add_on_says_so_rather_than_failing_oddly(
    without_extension: VectorStore,
) -> None:
    assert without_extension.available is False

    with pytest.raises(VectorStoreUnavailable) as failure:
        without_extension.require()

    message = str(failure.value)
    assert "Everything else in Sift works without it" in message
    assert "container" in message


async def test_the_questions_that_can_be_answered_without_it_are(
    without_extension: VectorStore,
) -> None:
    """Counting and forgetting are asked by screens and by tidying up, which run on every install.
    Neither has any reason to fail where the feature is simply absent."""
    assert await without_extension.count() == 0
    await without_extension.forget("whatever")
    await without_extension.clear()


async def test_searching_without_it_refuses_in_words(without_extension: VectorStore) -> None:
    with pytest.raises(VectorStoreUnavailable):
        await without_extension.nearest(unit(1.0), revision=REVISION, limit=5)


# --- keeping and finding --------------------------------------------------------------------


async def test_nothing_is_created_until_something_needs_it(
    temp_db: Database, store: VectorStore
) -> None:
    """Boot must not depend on an optional capability, so the table is made on first use."""
    found = await temp_db.fetch_one("SELECT name FROM sqlite_master WHERE name = 'semantic_frames'")
    assert found is None

    await store.ensure_ready()

    assert (
        await temp_db.fetch_one("SELECT name FROM sqlite_master WHERE name = 'semantic_frames'")
        is not None
    )


async def test_a_frame_is_found_by_something_that_looks_like_it(store: VectorStore) -> None:
    await store.put("clip", [(0, unit(1.0, 0.0))], revision=REVISION)
    await store.put("other", [(0, unit(0.0, 1.0))], revision=REVISION)

    found = await store.nearest(unit(1.0, 0.05), limit=5, revision=REVISION)

    assert [neighbour.asset_id for neighbour in found] == ["clip", "other"]
    assert found[0].distance < found[1].distance


async def test_a_search_keeps_to_the_frames_the_model_in_use_described(
    store: VectorStore,
) -> None:
    """A search keeps to the frames of the model in use."""
    await store.put("old", [(0, unit(1.0, 0.0))], revision="siglip2-before")
    await store.put("new", [(0, unit(0.9, 0.1))], revision=REVISION)

    found = await store.nearest(unit(1.0, 0.0), revision=REVISION, limit=5)

    assert [neighbour.asset_id for neighbour in found] == ["new"]
    assert await store.describes("old", revision=REVISION) == []
    assert await store.describes("old", revision="siglip2-before") != []


async def test_the_moment_comes_back_with_the_file(store: VectorStore) -> None:
    """Which is the whole reason a frame is kept rather than an average of them."""
    await store.put("clip", [(0, unit(0.0, 1.0)), (94_000, unit(1.0, 0.0))], revision=REVISION)

    found = await store.nearest(unit(1.0, 0.0), limit=5, revision=REVISION)

    assert found[0].asset_id == "clip"
    assert found[0].at_ms == 94_000


async def test_one_file_takes_one_place_in_the_answer(store: VectorStore) -> None:
    """A thirty-frame video is thirty neighbours. A page of results made of one clip thirty times
    is not a page of results."""
    await store.put(
        "long", [(index * 1000, unit(1.0, index / 100)) for index in range(30)], revision=REVISION
    )
    await store.put("short", [(0, unit(0.0, 1.0))], revision=REVISION)

    found = await store.nearest(unit(1.0, 0.0), limit=5, revision=REVISION)

    assert [neighbour.asset_id for neighbour in found] == ["long", "short"]
    # The frame kept is the closest one, which is also the moment worth jumping to.
    assert found[0].at_ms == 0


async def test_asking_for_fewer_files_than_there_are_stops_at_that_many(
    store: VectorStore,
) -> None:
    for index in range(6):
        await store.put(f"clip{index}", [(0, unit(1.0, index / 10))], revision=REVISION)

    assert len(await store.nearest(unit(1.0, 0.0), limit=3, revision=REVISION)) == 3


async def test_describing_a_file_again_replaces_what_was_held_about_it(
    store: VectorStore,
) -> None:
    """A model change or an interrupted pass must not leave two descriptions of one moment."""
    await store.put("clip", [(0, unit(1.0, 0.0)), (1000, unit(1.0, 0.1))], revision=REVISION)
    await store.put("clip", [(0, unit(0.0, 1.0))], revision=REVISION)

    assert await store.count() == 1
    found = await store.nearest(unit(0.0, 1.0), limit=5, revision=REVISION)
    assert found[0].at_ms == 0


async def test_a_description_of_the_wrong_width_is_refused_rather_than_stored(
    store: VectorStore,
) -> None:
    """Numbers of a different width come from a different model, and mixing them does not error on
    its own: it quietly returns wrong neighbours."""
    with pytest.raises(ValueError, match="this index holds"):
        await store.put("clip", [(0, [1.0, 0.0, 0.0])], revision=REVISION)


async def test_forgetting_a_file_takes_every_frame_of_it(store: VectorStore) -> None:
    await store.put("clip", [(0, unit(1.0, 0.0)), (1000, unit(1.0, 0.1))], revision=REVISION)
    await store.put("keep", [(0, unit(0.0, 1.0))], revision=REVISION)

    await store.forget("clip")

    assert await store.count() == 1
    assert [
        n.asset_id for n in await store.nearest(unit(1.0, 0.0), limit=5, revision=REVISION)
    ] == ["keep"]


async def test_forgetting_a_file_that_was_never_described_is_not_an_error(
    store: VectorStore,
) -> None:
    await store.forget("never-seen")

    assert await store.count() == 0


@pytest.fixture
def hearing() -> Iterator[list[StatementRun]]:
    """Every statement heard with SQLite's count of its steps, on connections opened now."""
    heard: list[StatementRun] = []
    statement_budget().heard = heard
    try:
        yield heard
    finally:
        statement_budget().heard = None


async def test_replacing_or_forgetting_a_file_reads_only_its_own_frames(
    tmp_path: Path, hearing: list[StatementRun]
) -> None:
    """The vector table's file column is read row by row, so a delete by it walks every frame
    held, under the writer, once for each file described."""
    others = 2000
    database = Database(tmp_path / "steps.sqlite3")
    await database.connect()
    try:
        await database.initialize_schema()
        store = VectorStore(database)
        await store.put("clip", [(0, unit(1.0)), (10, unit(1.0, 0.1))], revision=REVISION)
        async with database.write() as connection:
            await connection.executemany(
                "INSERT INTO semantic_frames(revision, asset_id, at_ms, embedding)"
                " VALUES (?, ?, ?, ?)",
                [(REVISION, f"other{n}", 0, _pack(unit(0.0, 1.0))) for n in range(others)],
            )
        hearing.clear()
        await store.put("clip", [(0, unit(0.0, 0.0, 1.0))], revision=REVISION)
        await store.forget("clip")
        frames_left = await store.nearest(unit(1.0), limit=5, revision=REVISION)
    finally:
        await database.close()

    on_frames = [run for run in hearing if run.name.startswith("delete:semantic_frames#")]
    assert len(on_frames) == 2
    assert sum(run.steps for run in on_frames) < others
    assert {one.asset_id for one in frames_left} <= {f"other{n}" for n in range(others)}


async def test_the_whole_index_can_be_thrown_away(store: VectorStore) -> None:
    """A separate, deliberate act: switching the feature off does not do this, because turning
    something off to see what it does should not cost hours of re-reading every file."""
    await store.put("clip", [(0, unit(1.0, 0.0))], revision=REVISION)

    await store.clear()

    assert await store.count() == 0


async def test_a_search_of_an_empty_index_finds_nothing_rather_than_failing(
    store: VectorStore,
) -> None:
    assert await store.nearest(unit(1.0, 0.0), limit=5, revision=REVISION) == []


# --- what one file looks like, as one set of numbers -----------------------------------------


async def test_a_file_is_described_by_the_average_of_its_frames(store: VectorStore) -> None:
    """A file is described by the average of its frames, pooled when asked."""
    await store.put("clip", [(0, unit(1.0, 0.0)), (1000, unit(0.0, 1.0))], revision=REVISION)

    described = await store.describes("clip", revision=REVISION)

    # Halfway between the two, scaled back to length one.
    assert described[0] == pytest.approx(0.7071, abs=1e-3)
    assert described[1] == pytest.approx(0.7071, abs=1e-3)


async def test_a_file_with_one_frame_is_described_by_that_frame(store: VectorStore) -> None:
    await store.put("clip", [(0, unit(1.0, 0.0))], revision=REVISION)

    described = await store.describes("clip", revision=REVISION)

    assert described[0] == pytest.approx(1.0)


async def test_a_file_nothing_has_described_has_no_description(store: VectorStore) -> None:
    """Not a failure: a fact about how far the background pass has got."""
    assert await store.describes("never-seen", revision=REVISION) == []


async def test_a_machine_without_the_add_on_describes_nothing(
    without_extension: VectorStore,
) -> None:
    assert await without_extension.describes("clip", revision=REVISION) == []


async def test_frames_that_cancel_each_other_out_describe_nothing(store: VectorStore) -> None:
    """Two opposite frames average to no direction at all, and a vector of nothing cannot be
    scaled. Empty is the honest answer; the alternative is a page of NaN."""
    await store.put(
        "clip", [(0, unit(1.0, 0.0)), (1000, [-1.0] + [0.0] * (DIMENSION - 1))], revision=REVISION
    )

    assert await store.describes("clip", revision=REVISION) == []


async def test_asking_questions_never_brings_the_table_into_existence(
    temp_db: Database, store: VectorStore
) -> None:
    """Asking questions never creates the table."""
    assert await store.count() == 0
    assert await store.describes("anything", revision=REVISION) == []
    assert await store.nearest(unit(1.0), limit=5, revision=REVISION) == []
    await store.forget("anything")
    await store.clear()

    found = await temp_db.fetch_one("SELECT name FROM sqlite_master WHERE name = 'semantic_frames'")
    assert found is None


async def test_describing_something_is_what_builds_it(
    temp_db: Database, store: VectorStore
) -> None:
    await store.put("clip", [(0, unit(1.0, 0.0))], revision=REVISION)

    assert (
        await temp_db.fetch_one("SELECT name FROM sqlite_master WHERE name = 'semantic_frames'")
        is not None
    )


async def test_a_file_with_no_frames_in_a_built_index_has_no_description(
    store: VectorStore,
) -> None:
    """The ordinary state on a library part-way through: the index exists and this file is simply
    not in it yet. Distinct from the index not existing at all, and both answer the same way."""
    await store.put("described", [(0, unit(1.0, 0.0))], revision=REVISION)

    assert await store.describes("not-yet", revision=REVISION) == []


# --- dropping what belongs to files that have gone --------------------------------------------


async def test_the_index_can_say_which_files_it_holds_anything_for(store: VectorStore) -> None:
    """The index lists files once each, not frames."""
    await store.put("clip", [(0, unit(1.0, 0.0)), (1000, unit(0.0, 1.0))], revision=REVISION)
    await store.put("still", [(0, unit(1.0, 1.0))], revision=REVISION)

    assert sorted(await store.held_ids()) == ["clip", "still"]


async def test_an_index_that_was_never_built_holds_nothing_rather_than_failing(
    store: VectorStore,
) -> None:
    """Asked on every sweep, including the first one on an install where nothing has been described
    yet. Reaching for a table that does not exist would fail the sweep before it started."""
    assert await store.held_ids() == []


async def test_a_machine_without_the_add_on_holds_nothing(
    without_extension: VectorStore,
) -> None:
    assert await without_extension.held_ids() == []


async def test_pruning_takes_every_frame_of_the_named_files_and_no_others(
    store: VectorStore,
) -> None:
    """Pruning takes every frame of the named files and no others."""
    await store.put("gone", [(0, unit(1.0, 0.0)), (1000, unit(0.9, 0.1))], revision=REVISION)
    await store.put("kept", [(0, unit(0.0, 1.0))], revision=REVISION)

    assert await store.prune(["gone"]) == 1

    assert await store.held_ids() == ["kept"]
    assert await store.describes("gone", revision=REVISION) == []
    assert await store.describes("kept", revision=REVISION) != []


async def test_a_long_prune_gives_the_writer_back_between_files(
    temp_db: Database, store: VectorStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every other write waits while one is open, so many files gone is many short writes."""
    from sift.slices.semantic import store as module

    for one in ("a", "b", "c"):
        await store.put(one, [(0, unit(1.0))], revision=REVISION)
    opened: list[int] = []
    write = temp_db.write

    def counted() -> Any:
        opened.append(1)
        return write()

    monkeypatch.setattr(temp_db, "write", counted)
    monkeypatch.setattr(module, "PRUNE_SECONDS", 0.0)

    # Bounded, so a write that forgets nothing fails here rather than spinning.
    assert await asyncio.wait_for(store.prune(["a", "b", "c"]), timeout=30) == 3

    assert len(opened) == 3
    assert await store.count() == 0


async def test_pruning_counts_files_rather_than_rows(store: VectorStore) -> None:
    """ "Removed 5,000 frames" says nothing about how much was thrown away. Two files, one of them
    thirty moments long, is two."""
    await store.put(
        "one", [(at, unit(1.0, 0.0)) for at in range(0, 30_000, 1000)], revision=REVISION
    )
    await store.put("two", [(0, unit(0.0, 1.0))], revision=REVISION)

    assert await store.prune(["one", "two"]) == 2


async def test_pruning_nothing_is_not_an_error(store: VectorStore) -> None:
    """The ordinary answer on every sweep of a library nobody has deleted from."""
    await store.put("kept", [(0, unit(1.0, 0.0))], revision=REVISION)

    assert await store.prune([]) == 0
    assert await store.held_ids() == ["kept"]


async def test_pruning_never_brings_the_table_into_existence(
    temp_db: Database, store: VectorStore
) -> None:
    """Pruning never creates the table."""
    assert await store.prune(["anything"]) == 0

    found = await temp_db.fetch_one("SELECT name FROM sqlite_master WHERE name = 'semantic_frames'")
    assert found is None


async def test_a_machine_without_the_add_on_prunes_nothing(
    without_extension: VectorStore,
) -> None:
    assert await without_extension.prune(["gone"]) == 0


async def test_a_large_ask_is_clamped_to_what_the_index_will_take(store: VectorStore) -> None:
    """A large ask is clamped to the index's `k` limit, which widening searches can pass."""
    await store.put("clip", [(0, unit(1.0, 0.0))], revision=REVISION)

    # Far past the limit, both before and after the margin is applied.
    found = await store.nearest(unit(1.0, 0.0), limit=K_LIMIT * 2, revision=REVISION)

    assert [n.asset_id for n in found] == ["clip"]


# --- letting go of a file that has ended ---------------------------------------------------------


async def test_the_vectors_of_a_deleted_file_are_cleared_when_the_asset_ends(
    temp_db: Database, store: VectorStore
) -> None:
    """A deleted file's vectors are cleared when the asset ends: `vec0` has no foreign key, so the
    store is registered with `forget_everywhere`."""
    import sift.main  # noqa: F401 (imported for its side effect: the stores register)

    await store.put("gone", [(0, unit(1.0))], revision=REVISION)
    await store.put("kept", [(0, unit(0.0, 1.0))], revision=REVISION)
    assert sorted(await store.held_ids()) == ["gone", "kept"]

    cleared = await forget_everywhere(temp_db, ("gone",))

    assert cleared["vector-index"] == 1
    assert await store.held_ids() == ["kept"]


async def test_an_install_that_cannot_load_the_add_on_is_not_a_failed_delete(
    temp_db: Database, without_extension: VectorStore
) -> None:
    """Without the add-on, a delete is not a failure: `prune` answers zero."""
    assert await without_extension.prune(["gone"]) == 0


# --- the pooled description, and the models that are no longer in use ----------------------------


async def _pooled_rows(database: Database) -> list[tuple[str, str]]:
    rows = await database.fetch_all("SELECT asset_id, revision FROM semantic_pooled")
    return sorted((str(row["asset_id"]), str(row["revision"])) for row in rows)


async def test_describing_a_file_keeps_its_whole_description_beside_the_frames(
    temp_db: Database, store: VectorStore
) -> None:
    """Describing a file keeps its pooled description beside the frames."""
    await store.put("clip", [(0, unit(1.0)), (10, unit(0.0, 1.0))], revision=REVISION)

    assert await _pooled_rows(temp_db) == [("clip", REVISION)]


async def test_the_pooled_description_is_the_average_the_frames_always_meant(
    temp_db: Database, store: VectorStore
) -> None:
    """The stored row and the arithmetic it replaces are one rule, so they cannot drift."""
    await store.put("clip", [(0, unit(1.0)), (10, unit(0.0, 1.0))], revision=REVISION)

    stored = await store.describes("clip", revision=REVISION)

    root = 1.0 / math.sqrt(2.0)
    assert stored[0] == pytest.approx(root)
    assert stored[1] == pytest.approx(root)


async def test_a_file_described_before_the_pooled_table_is_read_once_and_written_down(
    temp_db: Database, store: VectorStore
) -> None:
    """A file described before the pooled table is pooled on first ask and written down."""
    await store.put("clip", [(0, unit(1.0))], revision=REVISION)
    await temp_db.execute("DELETE FROM semantic_pooled")
    assert await _pooled_rows(temp_db) == []

    first = await store.describes("clip", revision=REVISION)

    assert first[0] == pytest.approx(1.0)
    assert await _pooled_rows(temp_db) == [("clip", REVISION)]


async def test_throwing_the_index_away_takes_the_pooled_descriptions_with_it(
    temp_db: Database, store: VectorStore
) -> None:
    """A pooled row left behind would keep answering "this is what that file looks like" out of an
    index holding not one of the frames it was averaged from."""
    await store.put("clip", [(0, unit(1.0))], revision=REVISION)

    await store.clear()

    assert await _pooled_rows(temp_db) == []
    assert await store.describes("clip", revision=REVISION) == []


async def test_pruning_a_file_takes_its_pooled_description_too(
    temp_db: Database, store: VectorStore
) -> None:
    """It has no foreign key on purpose (see the schema), so the sweep is what clears it, at the
    same moment the frames it is an average of go."""
    await store.put("gone", [(0, unit(1.0))], revision=REVISION)
    await store.put("kept", [(0, unit(0.0, 1.0))], revision=REVISION)

    await store.prune(["gone"])

    assert await _pooled_rows(temp_db) == [("kept", REVISION)]


async def test_the_index_says_which_models_numbers_it_is_holding(store: VectorStore) -> None:
    """The survey behind the purge: one row per model, which on an install that has never changed
    model is one row."""
    await store.put("old", [(0, unit(1.0))], revision="older-model")
    await store.put("new", [(0, unit(1.0)), (5, unit(0.0, 1.0))], revision=REVISION)

    assert await store.revisions_held() == {"older-model": 1, REVISION: 2}


async def test_a_purge_drops_every_model_but_the_one_in_use(
    temp_db: Database, store: VectorStore
) -> None:
    """A purge drops every model's numbers but the one in use."""
    await store.put("old", [(0, unit(1.0))], revision="older-model")
    await store.put("new", [(0, unit(0.0, 1.0))], revision=REVISION)

    dropped = await store.purge_other_revisions(REVISION)

    assert dropped == 1
    assert await store.revisions_held() == {REVISION: 1}
    assert await _pooled_rows(temp_db) == [("new", REVISION)]


async def test_a_purge_with_nothing_stale_takes_nothing(store: VectorStore) -> None:
    """The ordinary state of an install that has never changed model."""
    await store.put("clip", [(0, unit(1.0))], revision=REVISION)

    assert await store.purge_other_revisions(REVISION) == 0
    assert await store.revisions_held() == {REVISION: 1}


async def test_a_machine_without_the_add_on_holds_no_models_and_purges_nothing(
    without_extension: VectorStore,
) -> None:
    """Every method is callable on a machine that cannot load the add-on, which is most of them."""
    assert await without_extension.revisions_held() == {}
    assert await without_extension.purge_other_revisions(REVISION) == 0


async def test_many_files_are_described_in_one_read_where_the_record_vouches_for_them(
    store: VectorStore, temp_db: Database
) -> None:
    """The Shoots pass's read: a pool's numbers in one read. A file the record says the model in use
    described is in the answer (a pooled row, or the frames pooled on the way past for one
    described before the pooled table); a file with no record at this revision is not."""
    from sift.slices.semantic.records import Records

    records = Records(temp_db)
    await store.put("one", [(0, unit(1.0, 0.0))], revision=REVISION)
    await store.put("two", [(0, unit(0.0, 1.0))], revision=REVISION)
    await store.put("stale", [(0, unit(1.0, 1.0))], revision=REVISION)
    for asset_id in ("one", "two"):
        await temp_db.execute(
            "INSERT INTO assets (id, identity, media_type, size_bytes, added_at) "
            "VALUES (?, ?, 'image', 1, 1700000000)",
            (asset_id, f"digest-{asset_id}"),
        )
    await records.mark("one", revision=REVISION, frames=1, at_ms=1)
    await records.mark("two", revision=REVISION, frames=1, at_ms=1)
    await temp_db.execute("DELETE FROM semantic_pooled WHERE asset_id = 'two'")

    found = await store.describes_many(["one", "two", "stale", "never"], revision=REVISION)

    assert sorted(found) == ["one", "two"]
    assert found["one"][0] == pytest.approx(1.0)
    assert found["two"][1] == pytest.approx(1.0)
    assert await store.describes_many([], revision=REVISION) == {}


async def test_a_file_described_with_no_frames_has_no_description_at_all(
    store: VectorStore, temp_db: Database
) -> None:
    """A file the model looked at and drew no frame from (nothing decodable in it) is recorded
    as described, keeps no whole-file row, and is absent from the answer rather than present as an
    empty list that would compare as near to everything."""
    from sift.slices.semantic.records import Records

    await store.put("blank", [], revision=REVISION)
    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, size_bytes, added_at) "
        "VALUES ('blank', 'digest-blank', 'video', 1, 1700000000)"
    )
    await Records(temp_db).mark("blank", revision=REVISION, frames=0, at_ms=1)

    assert await store.describes_many(["blank"], revision=REVISION) == {}
    assert await temp_db.fetch_all("SELECT 1 FROM semantic_pooled WHERE asset_id = 'blank'") == []


# --- the keys and the files' index ---------------------------------------------------------------


async def _keys(database: Database) -> tuple[list[str], list[str]]:
    frames = await database.fetch_all("SELECT asset_id FROM semantic_frame_keys ORDER BY frame")
    files = await database.fetch_all("SELECT asset_id FROM semantic_file_keys ORDER BY asset_id")
    return [str(row["asset_id"]) for row in frames], [str(row["asset_id"]) for row in files]


async def _files_held(database: Database) -> int:
    (row,) = await database.fetch_all("SELECT COUNT(*) AS total FROM semantic_files")
    return int(row["total"])


async def test_every_vector_is_kept_by_its_files_key(temp_db: Database, store: VectorStore) -> None:
    await store.put("clip", [(0, unit(1.0)), (10, unit(0.0, 1.0))], revision=REVISION)
    await store.put("still", [(0, unit(1.0))], revision=REVISION)
    await store.put("clip", [(0, unit(1.0))], revision=REVISION)

    assert await _keys(temp_db) == (["still", "clip"], ["clip", "still"])
    assert await _files_held(temp_db) == 2


async def test_forgetting_pruning_and_clearing_take_the_keys(
    temp_db: Database, store: VectorStore
) -> None:
    """A key left behind would scope a search to a vector that is gone, or rank a file twice."""
    for one in ("a", "b", "c"):
        await store.put(one, [(0, unit(1.0))], revision=REVISION)

    await store.forget("a")
    await store.prune(["b"])
    assert await _keys(temp_db) == (["c"], ["c"])
    assert await _files_held(temp_db) == 1

    await store.clear()
    assert await _keys(temp_db) == ([], [])
    assert await _files_held(temp_db) == 0


async def test_a_file_is_ranked_by_its_whole_description(store: VectorStore) -> None:
    await store.put("clip", [(0, unit(1.0)), (10, unit(0.0, 1.0))], revision=REVISION)
    await store.put("near", [(0, unit(1.0, 0.9))], revision=REVISION)
    await store.put("far", [(0, unit(0.0, 0.0, 1.0))], revision=REVISION)

    found = await store.nearest_files(unit(1.0, 1.0), revision=REVISION, limit=2)

    assert [one for one, _ in found] == ["clip", "near"]
    assert found[0][1] == pytest.approx(0.0, abs=1e-6)


async def test_ranking_files_of_an_index_never_built_finds_nothing(store: VectorStore) -> None:
    assert await store.nearest_files(unit(1.0), revision=REVISION, limit=5) == ()


async def test_a_purge_takes_the_old_models_keys_and_files(
    temp_db: Database, store: VectorStore
) -> None:
    await store.put("old", [(0, unit(1.0))], revision="an-older-model")
    await store.put("new", [(0, unit(1.0))], revision=REVISION)

    await store.purge_other_revisions(REVISION)

    assert await _keys(temp_db) == (["new"], ["new"])
    assert await _files_held(temp_db) == 1


async def test_every_write_moves_the_index_mark(store: VectorStore) -> None:
    from sift.slices.semantic.store import index_writes

    before = index_writes()
    await store.put("clip", [(0, unit(1.0))], revision=REVISION)
    await store.forget("clip")

    assert index_writes() == before + 2


async def _as_before_the_keys(database: Database) -> None:
    """The index as an older Sift left it: frames, some pooled rows, no keys, no files' index."""
    async with database.write() as connection:
        await connection.execute("DELETE FROM semantic_frame_keys")
        await connection.execute("DELETE FROM semantic_file_keys")
        await connection.execute("DROP TABLE semantic_files")
        await connection.execute("DELETE FROM semantic_pooled WHERE asset_id = 'clip'")


async def test_an_index_made_before_the_keys_is_brought_forward(
    temp_db: Database, store: VectorStore
) -> None:
    from sift.slices.semantic.store import index_files

    await store.put("clip", [(0, unit(1.0)), (10, unit(0.0, 1.0))], revision=REVISION)
    await store.put("still", [(0, unit(0.0, 1.0))], revision=REVISION)
    # One more frame of the first file after the second's: its frames are not one run.
    async with temp_db.write() as connection:
        await connection.execute(
            "INSERT INTO semantic_frames(revision, asset_id, at_ms, embedding) VALUES (?, ?, ?, ?)",
            (REVISION, "clip", 20, _pack(unit(1.0))),
        )
    await _as_before_the_keys(temp_db)

    async with temp_db.write() as connection:
        await index_files(connection)

    assert await _keys(temp_db) == (["clip", "clip", "still", "clip"], ["clip", "still"])
    pooled = await store.describes("clip", revision=REVISION)
    assert pooled[:2] == [pytest.approx(2 / math.sqrt(5.0)), pytest.approx(1 / math.sqrt(5.0))]
    found = await store.nearest_files(unit(1.0, 1.0), revision=REVISION, limit=5)
    assert [one for one, _ in found] == ["clip", "still"]


async def test_the_add_ons_storage_is_read_whole_and_agrees_with_the_table(
    temp_db: Database, store: VectorStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read by chunk only on the layout this was written against; a new layout reads row by row."""
    from sift.slices.semantic import store as module
    from sift.slices.semantic.store import _Frames

    await store.put("clip", [(0, unit(1.0)), (10, unit(0.0, 1.0))], revision=REVISION)
    async with temp_db.write() as connection:
        keys = [
            int(row[0])
            for row in await connection.execute_fetchall(
                "SELECT frame FROM semantic_frame_keys ORDER BY frame", ()
            )
        ]
        frames = _Frames(connection)
        await frames.settle(keys[0])
        await frames.between(keys[0], keys[-1])
        by_chunk = await frames.read(keys)

    assert frames._chunked
    assert by_chunk == [_pack(unit(1.0)), _pack(unit(0.0, 1.0))]

    # A layout this was not written against: nothing is looked up, every frame read by row.
    monkeypatch.setattr(module, "_LAYOUT", "SELECT 0")
    async with temp_db.write() as connection:
        by_row = _Frames(connection)
        await by_row.settle(keys[0])
        await by_row.between(keys[0], keys[-1])
        assert not by_row._chunked and not by_row._slots
        assert await by_row.read(keys) == by_chunk


async def test_an_index_this_machine_cannot_read_is_described_again(
    temp_db: Database, store: VectorStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sift.slices.semantic import store as module

    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, size_bytes, added_at) "
        "VALUES ('clip', 'digest-clip', 'image', 1, 1700000000)"
    )
    await temp_db.execute(
        "INSERT INTO semantic_indexed (asset_id, revision, frames, indexed_at) "
        "VALUES ('clip', ?, 1, 1)",
        (REVISION,),
    )
    await store.put("clip", [(0, unit(1.0))], revision=REVISION)
    monkeypatch.setattr(module, "_ADD_ON_HERE", "SELECT 1 WHERE 0")

    async with temp_db.write() as connection:
        await module.index_files(connection)

    assert await temp_db.fetch_all("SELECT asset_id FROM semantic_indexed") == []


async def test_a_file_of_consecutive_frames_is_pooled_once_and_an_empty_one_not_at_all(
    temp_db: Database, store: VectorStore
) -> None:
    """Two frames in a run are one file; a file whose frames pool to nothing has no index row."""
    from sift.slices.semantic.store import index_files

    await store.put("clip", [(0, unit(1.0)), (10, unit(0.0, 1.0))], revision=REVISION)
    await store.put("still", [(0, unit(0.0, 1.0))], revision=REVISION)
    await _as_before_the_keys(temp_db)
    async with temp_db.write() as connection:
        await connection.execute("DELETE FROM semantic_pooled")
        await connection.execute(
            "INSERT INTO semantic_frames(revision, asset_id, at_ms, embedding) VALUES (?, ?, ?, ?)",
            (REVISION, "blank", 0, _pack([0.0] * DIMENSION)),
        )

    async with temp_db.write() as connection:
        await index_files(connection)

    assert await _pooled_rows(temp_db) == [("clip", REVISION), ("still", REVISION)]
    assert await _keys(temp_db) == (["clip", "clip", "still", "blank"], ["clip", "still"])


async def test_a_file_whose_frames_go_on_in_the_next_batch_is_one_file(
    temp_db: Database, store: VectorStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sift.slices.semantic import store as module

    await store.put(
        "clip", [(0, unit(1.0)), (10, unit(1.0)), (20, unit(0.0, 1.0))], revision=REVISION
    )
    await _as_before_the_keys(temp_db)
    monkeypatch.setattr(module, "INDEX_BATCH", 2)

    async with temp_db.write() as connection:
        await module.index_files(connection)

    assert await _keys(temp_db) == (["clip", "clip", "clip"], ["clip"])
    assert await _files_held(temp_db) == 1
