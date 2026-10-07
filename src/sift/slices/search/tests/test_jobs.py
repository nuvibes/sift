# SPDX-License-Identifier: AGPL-3.0-or-later
"""The reindex job as a worker meets it: the registered handler runs the reindex, and a library
bigger than one batch is indexed whole."""

from __future__ import annotations

import contextlib
import time
from contextlib import AbstractAsyncContextManager

import pytest

from sift.kernel.access import index_assets, search_index
from sift.kernel.db import Connection, Database
from sift.kernel.ids import new_id
from sift.kernel.jobs import JobContext, JobQueue, JobState, registered_handlers
from sift.kernel.version import app_version
from sift.slices.search import jobs as jobs_module
from sift.slices.search.jobs import (
    ASSET_IDS,
    FTS_REINDEX,
    FTS_REINDEX_FILES,
    _what_it_is_doing,
    catch_up_if_behind,
    ensure_scheduled,
    register_handlers,
)

_INSERT = """
INSERT INTO assets (id, identity, media_type, size_bytes, original_filename, added_at)
VALUES (?, ?, 'video', 1, ?, 0)
"""


@pytest.fixture
async def database(temp_db: Database) -> Database:
    """A database with the schema on it. `temp_db` is a connection, not a built library."""
    await temp_db.initialize_schema()
    return temp_db


async def _seed(database: Database, count: int) -> list[str]:
    ids = []
    async with database.write() as connection:
        for number in range(count):
            asset_id = new_id()
            ids.append(asset_id)
            await connection.execute(
                _INSERT, (asset_id, f"digest-{number}", f"clip_{number:04d}.mp4")
            )
    return ids


async def _indexed(database: Database) -> set[str]:
    rows = await database.fetch_all("SELECT asset_id FROM assets_fts")
    return {row["asset_id"] for row in rows}


async def test_a_library_bigger_than_one_batch_is_indexed_whole(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A library bigger than one batch is indexed whole, with the batch shrunk."""
    monkeypatch.setattr(search_index, "BATCH", 3)
    ids = await _seed(database, 7)

    written = await index_assets(database, rebuild=True)

    assert written == 7
    assert await _indexed(database) == set(ids)


async def test_a_library_that_is_exactly_one_batch_stops_without_a_second_pass(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The boundary the loop is most likely to get wrong in the other direction."""
    monkeypatch.setattr(search_index, "BATCH", 3)
    ids = await _seed(database, 3)

    assert await index_assets(database, rebuild=True) == 3
    assert await _indexed(database) == set(ids)


async def test_an_empty_library_indexes_to_an_empty_index(database: Database) -> None:
    assert await index_assets(database, rebuild=True) == 0
    assert await _indexed(database) == set()


async def test_the_registered_handler_is_the_one_that_does_the_indexing(
    database: Database, job_queue: JobQueue
) -> None:
    """The registered handler is the one that indexes."""
    ids = await _seed(database, 2)
    register_handlers(database=database)
    assert FTS_REINDEX in registered_handlers()

    await job_queue.enqueue(FTS_REINDEX, {})
    job = await job_queue.claim("a-worker")
    assert job is not None

    await registered_handlers()[FTS_REINDEX](
        JobContext(job=job, worker_id="a-worker", queue=job_queue)
    )

    assert await _indexed(database) == set(ids)


async def test_the_registered_files_handler_indexes_exactly_the_files_it_names(
    database: Database, job_queue: JobQueue
) -> None:
    ids = await _seed(database, 3)
    register_handlers(database=database)

    await job_queue.enqueue(FTS_REINDEX_FILES, {ASSET_IDS: ids[:2]})
    job = await job_queue.claim("a-worker")
    assert job is not None

    await registered_handlers()[FTS_REINDEX_FILES](
        JobContext(job=job, worker_id="a-worker", queue=job_queue)
    )

    assert await _indexed(database) == set(ids[:2])


# --- saying what it is doing, and why -----------------------------------------------------------
#
# A pass started by an upgrade says how big it is and why it started.


async def test_the_pass_says_how_big_it_is_before_it_starts(
    database: Database, job_queue: JobQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The note carries the size, counted before a row is written."""
    await _seed(database, 3)
    register_handlers(database=database)
    await job_queue.enqueue(FTS_REINDEX, {})
    job = await job_queue.claim("a-worker")
    assert job is not None

    # Read from inside the pass: the note is replaced at the end.
    during: list[str | None] = []
    real = index_assets

    async def spy(*args: object, **kwargs: object) -> int:
        watched = await job_queue.get(job.id)
        during.append(None if watched is None else watched.note)
        return await real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(jobs_module, "index_assets", spy)
    await registered_handlers()[FTS_REINDEX](
        JobContext(job=job, worker_id="a-worker", queue=job_queue)
    )

    assert during and during[0], "the pass said nothing at all about what it was doing"
    said = during[0]
    assert said is not None
    assert "3 files" in said, said
    assert "Rebuilding the search index" in said, said
    # And what it really wrote at the end, which is not always what it expected.
    after = await job_queue.get(job.id)
    assert after is not None and after.note == "Indexed 3 files"


@pytest.mark.unit
def test_the_sentence_names_the_version_so_an_unasked_for_pass_reads_as_an_upgrade() -> None:
    """Why it is happening is the half a count cannot answer. A number somebody can compare with the
    one on the About screen is what makes this read as an upgrade finishing."""
    said = _what_it_is_doing(1, catching_up=False)

    if app_version():
        assert said.startswith(f"Catching up after the upgrade to {app_version()}"), said
    else:
        # A checkout has no version at all and must not invent one.
        assert "upgrade" not in said, said
    assert "1 file" in said, "a count of one was pluralised"


@pytest.mark.unit
@pytest.mark.parametrize("version", ["", "0.2.0"])
def test_a_checkout_says_the_sentence_without_a_version_and_an_install_names_its_own(
    version: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both halves on every machine: the test above can only take the branch its own tree has."""
    from sift.slices.search import jobs as search_jobs

    monkeypatch.setattr(search_jobs, "app_version", lambda: version)

    said = _what_it_is_doing(2, catching_up=False)

    if version:
        assert said.startswith("Catching up after the upgrade to 0.2.0"), said
    else:
        assert said == "Rebuilding the search index \u2014 2 files", said


@pytest.mark.unit
def test_the_catch_up_and_the_rebuild_are_not_described_the_same_way() -> None:
    """Two different passes wear one job type, and a person watching should be told which."""
    assert "Indexing what has arrived" in _what_it_is_doing(5, catching_up=True)
    assert "Rebuilding the search index" in _what_it_is_doing(5, catching_up=False)


# --- queueing only when there is work -----------------------------------------------------------
#
# Each registers the handler first: the queue refuses a type nothing can run.


async def test_a_boot_over_an_indexed_library_queues_nothing(
    database: Database, job_queue: JobQueue
) -> None:
    """An idle Sift looks idle. A pending job should mean there is really something to do: the
    rule the bin's sweep states in as many words, and the one a refresh on a timer breaks."""
    register_handlers(database=database)
    await _seed(database, 2)
    await index_assets(database, rebuild=True)

    await ensure_scheduled(queue=job_queue, database=database)

    assert (await job_queue.list(job_type=FTS_REINDEX)).total == 0


async def test_a_boot_over_a_library_that_is_behind_queues_one_pass(
    database: Database, job_queue: JobQueue
) -> None:
    """The other side, so the test above cannot pass by never queueing anything."""
    register_handlers(database=database)
    await _seed(database, 2)

    await ensure_scheduled(queue=job_queue, database=database)

    assert (await job_queue.list(job_type=FTS_REINDEX)).total == 1


async def test_a_second_boot_does_not_pile_up_another_pass(
    database: Database, job_queue: JobQueue
) -> None:
    """Without the guard every restart would add one, until the queue was nothing else."""
    register_handlers(database=database)
    await _seed(database, 2)

    await ensure_scheduled(queue=job_queue, database=database)
    await ensure_scheduled(queue=job_queue, database=database)

    assert (await job_queue.list(job_type=FTS_REINDEX)).total == 1


async def test_a_rename_stands_down_only_for_a_pass_that_would_carry_it(
    database: Database, job_queue: JobQueue
) -> None:
    """A rename stands down only for a whole-library pass, never a catch-up; the second half keeps a
    guard that never queues from passing."""
    from sift.slices.search.jobs import CATCH_UP, SCOPE
    from sift.slices.search.reindex import Reindexer

    register_handlers(database=database)
    seam = Reindexer(database=database, queue=job_queue)
    await job_queue.enqueue(FTS_REINDEX, {SCOPE: CATCH_UP})

    await seam.renamed()

    assert (await job_queue.list(job_type=FTS_REINDEX)).total == 2, (
        "a rename stood down for a catch-up, which cannot see what it changed"
    )

    # The pass just queued IS the one that re-reads every asset, so a second rename adds nothing.
    await seam.renamed()

    assert (await job_queue.list(job_type=FTS_REINDEX)).total == 2


async def test_a_search_asks_for_a_catch_up_only_while_one_is_needed(
    database: Database, job_queue: JobQueue
) -> None:
    """What keeps the index current, and what keeps it quiet, are the same check.

    Queued while something is unindexed; not queued once nothing is; and never twice over, so a
    busy library ends up with one pending pass rather than one per search.
    """
    register_handlers(database=database)
    await _seed(database, 2)

    assert await catch_up_if_behind(queue=job_queue, database=database) is True
    # A second search while the first pass is still waiting adds nothing.
    assert await catch_up_if_behind(queue=job_queue, database=database) is False
    assert (await job_queue.list(job_type=FTS_REINDEX)).total == 1

    # Once the work is done, searching asks for nothing at all.
    await index_assets(database, rebuild=True)
    claimed = await job_queue.claim("a-worker")
    assert claimed is not None
    await job_queue.complete(claimed.id, "a-worker")

    assert await catch_up_if_behind(queue=job_queue, database=database) is False


async def test_recording_a_search_works_without_a_queue_to_ask(database: Database) -> None:
    """Recording a search works without a queue."""
    from sift.kernel.access import Repository, Role
    from sift.kernel.config import get_settings
    from sift.kernel.content import ContentStore
    from sift.slices.search.filters import FilterCompiler
    from sift.slices.search.service import SearchService
    from sift.testing.fixtures import create_user

    await _seed(database, 1)
    await index_assets(database, rebuild=True)

    access = Repository(database, ContentStore(database, get_settings()))
    service = SearchService(database, access, FilterCompiler(access))

    viewer = await create_user(database, Role.ADMIN)
    await service.remember(viewer, "clip")

    assert [row.label for row in await service.recent(viewer)] == ["clip"]


async def test_recording_a_search_is_what_asks_the_index_to_catch_up(
    database: Database, job_queue: JobQueue
) -> None:
    """Submitting a search asks for a catch-up while the index is behind; the empty query asks
    for nothing."""
    from sift.kernel.access import Repository, Role
    from sift.kernel.config import get_settings
    from sift.kernel.content import ContentStore
    from sift.slices.search.filters import FilterCompiler
    from sift.slices.search.service import SearchService
    from sift.testing.fixtures import create_user

    register_handlers(database=database)
    await _seed(database, 2)

    access = Repository(database, ContentStore(database, get_settings()))
    service = SearchService(database, access, FilterCompiler(access), queue=job_queue)
    viewer = await create_user(database, Role.ADMIN)

    await service.remember(viewer, "   ")
    assert (await job_queue.list(job_type=FTS_REINDEX)).total == 0

    await service.remember(viewer, "clip")
    assert (await job_queue.list(job_type=FTS_REINDEX)).total == 1

    # A second search while the first pass is still waiting adds nothing.
    await service.remember(viewer, "another")
    assert (await job_queue.list(job_type=FTS_REINDEX)).total == 1


async def test_picking_something_out_of_the_dropdown_asks_the_index_for_nothing(
    database: Database, job_queue: JobQueue
) -> None:
    """Picking from the dropdown asks the index for nothing: nothing was searched."""
    from sift.kernel.access import Repository, Role
    from sift.kernel.config import get_settings
    from sift.kernel.content import ContentStore
    from sift.slices.search.filters import FilterCompiler
    from sift.slices.search.service import SearchService
    from sift.testing.fixtures import create_user

    register_handlers(database=database)
    await _seed(database, 2)

    access = Repository(database, ContentStore(database, get_settings()))
    service = SearchService(database, access, FilterCompiler(access), queue=job_queue)
    viewer = await create_user(database, Role.ADMIN)

    await service.remember_pick(viewer, "people", "p1", "Orla Fennimore")

    assert [row.label for row in await service.recent(viewer)] == ["Orla Fennimore"]
    assert (await job_queue.list(job_type=FTS_REINDEX)).total == 0

    # The control: the same service DOES ask when a search is what happened, so the assertion above
    # is about the pick rather than about a queue that was never going to be written to.
    await service.remember(viewer, "clip")
    assert (await job_queue.list(job_type=FTS_REINDEX)).total == 1


async def test_a_named_set_indexes_exactly_those_assets(database: Database) -> None:
    """The multi-id path, which exists so a bulk write is one transaction rather than one each."""
    ids = await _seed(database, 5)
    chosen = ids[1:4]

    written = await index_assets(database, asset_ids=chosen)

    assert written == 3
    assert await _indexed(database) == set(chosen)


async def test_a_named_set_spanning_more_than_one_batch_is_indexed_whole(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The paging loop again, this time with the set filter applied: the filter filters the rows
    the loop walks, so a set larger than a batch is where the two could disagree."""
    monkeypatch.setattr(search_index, "BATCH", 2)
    ids = await _seed(database, 7)
    chosen = ids[:5]

    assert await index_assets(database, asset_ids=chosen) == 5
    assert await _indexed(database) == set(chosen)


async def test_an_empty_set_writes_nothing_and_opens_no_transaction(database: Database) -> None:
    """An empty set opens no transaction, which the row count alone cannot show."""
    await _seed(database, 3)
    opened = 0
    real_write = database.write

    def counting_write() -> AbstractAsyncContextManager[Connection]:
        nonlocal opened
        opened += 1
        return real_write()

    database.write = counting_write  # type: ignore[method-assign,assignment]
    try:
        assert await index_assets(database, asset_ids=[]) == 0
    finally:
        database.write = real_write  # type: ignore[method-assign]

    assert opened == 0, "it opened a write transaction to index nothing"
    assert await _indexed(database) == set()


async def test_a_named_set_replaces_rather_than_duplicates(database: Database) -> None:
    """Indexing the same set twice leaves one row each. An FTS5 table has no primary key, so
    nothing but the delete-before-insert stops the same asset matching twice."""
    ids = await _seed(database, 3)

    await index_assets(database, asset_ids=ids)
    await index_assets(database, asset_ids=ids)

    rows = await database.fetch_all("SELECT asset_id FROM assets_fts")
    assert len(rows) == 3


async def test_a_search_made_after_the_clock_went_back_is_still_the_newest(
    database: Database,
) -> None:
    """The history is ordered by the minted id, not the clock, so a search after the clock went
    back is still the newest."""
    from sift.kernel.access import Repository, Role
    from sift.kernel.config import get_settings
    from sift.kernel.content import ContentStore
    from sift.slices.search.filters import FilterCompiler
    from sift.slices.search.service import SearchService
    from sift.testing.fixtures import create_user

    await _seed(database, 1)
    access = Repository(database, ContentStore(database, get_settings()))
    # Forward, forward, then a step back that lands the last search before the first two.
    ticks = iter([2_000.0, 2_001.0, 1_000.0])
    last = [1_000.0]

    def stepping() -> float:
        with contextlib.suppress(StopIteration):
            last[0] = next(ticks)
        return last[0]

    service = SearchService(database, access, FilterCompiler(access), clock=stepping)
    viewer = await create_user(database, Role.ADMIN)

    await service.remember(viewer, "first")
    await service.remember(viewer, "second")
    await service.remember(viewer, "third")

    assert [row.label for row in await service.recent(viewer)] == [
        "third",
        "second",
        "first",
    ], "the newest search sank below the older ones because its clock reading was smaller"


async def test_the_trim_keeps_the_newest_searches_when_the_clock_has_gone_back(
    database: Database,
) -> None:
    """The trim keeps the newest searches when the clock has gone back."""
    from sift.kernel.access import Repository, Role
    from sift.kernel.config import get_settings
    from sift.kernel.content import ContentStore
    from sift.slices.search.filters import FilterCompiler
    from sift.slices.search.service import MAX_HISTORY, SearchService
    from sift.testing.fixtures import create_user

    await _seed(database, 1)
    access = Repository(database, ContentStore(database, get_settings()))
    # The clock runs forward for the first batch and then jumps back for the last five, as a
    # machine's clock can.
    readings = [2_000.0 + n for n in range(MAX_HISTORY)] + [1_000.0] * 5
    ticks = iter(readings)
    last = [readings[-1]]

    def stepping() -> float:
        with contextlib.suppress(StopIteration):
            last[0] = next(ticks)
        return last[0]

    service = SearchService(database, access, FilterCompiler(access), clock=stepping)
    viewer = await create_user(database, Role.ADMIN)

    for number in range(MAX_HISTORY + 5):
        await service.remember(viewer, f"query number {number}")

    kept = [row.label for row in await service.recent(viewer, limit=MAX_HISTORY)]

    assert len(kept) == MAX_HISTORY
    assert kept[0] == f"query number {MAX_HISTORY + 4}", (
        "the newest search was deleted by the trim, because its clock reading was the smallest"
    )
    newest_five = {f"query number {MAX_HISTORY + n}" for n in range(5)}
    assert newest_five <= set(kept), "the searches made after the correction were the ones lost"


# --- the horizon on the record -----------------------------------------------------------------
#
#
# The search record is kept by age rather than count.


class _Rule:
    """A settings seam holding one number, which is all the sweep reads."""

    def __init__(self, days: object) -> None:
        self._days = days

    async def get_app(self, key: str) -> object:
        assert key == jobs_module.KEEP_DAYS_KEY
        return self._days

    async def get_user(self, user_id: str, key: str) -> object:  # pragma: no cover (unused here)
        raise AssertionError("the sweep reads no per-user setting")


async def _record(database: Database, row_id: str, user_id: str, at: int) -> None:
    async with database.write() as connection:
        await connection.execute(
            "INSERT INTO search_events (id, user_id, kind, subject, at)"
            " VALUES (?, ?, 'query', 'beach', ?)",
            (row_id, user_id, at),
        )


async def _kept(database: Database) -> list[str]:
    found = await database.fetch_all("SELECT id FROM search_events ORDER BY id")
    return [str(row["id"]) for row in found]


async def test_the_sweep_deletes_what_is_past_the_horizon_and_nothing_else(
    database: Database, job_queue: JobQueue
) -> None:
    from sift.kernel.access import Role
    from sift.testing.fixtures import create_user

    viewer = await create_user(database, Role.ADMIN)
    now = int(time.time())
    await _record(database, "old", viewer.id, now - 400 * 24 * 60 * 60)
    await _record(database, "recent", viewer.id, now - 10 * 24 * 60 * 60)

    register_handlers(database=database, preferences=_Rule(365), queue=job_queue)
    await job_queue.enqueue(jobs_module.SEARCH_EVENTS_PRUNE, {})
    job = await job_queue.claim("a-worker")
    assert job is not None
    await registered_handlers()[jobs_module.SEARCH_EVENTS_PRUNE](
        JobContext(job=job, worker_id="a-worker", queue=job_queue)
    )

    assert await _kept(database) == ["recent"]
    # The scheduler places the next run (`kernel/tests/test_task_clock.py`), never the sweep.
    coming = await job_queue.list(job_type=jobs_module.SEARCH_EVENTS_PRUNE, state=JobState.QUEUED)
    assert coming.total == 0


async def test_nothing_is_swept_while_the_rule_is_off(
    database: Database, job_queue: JobQueue
) -> None:
    """Zero is keep everything, also for a PRESS, which always runs. The rule decides WHAT the
    sweep deletes; whether it runs on its own is the task's When and the scheduler's business."""
    from sift.kernel.access import Role
    from sift.testing.fixtures import create_user

    viewer = await create_user(database, Role.ADMIN)
    await _record(database, "ancient", viewer.id, 0)
    register_handlers(database=database, preferences=_Rule(0), queue=job_queue)

    await job_queue.enqueue(jobs_module.SEARCH_EVENTS_PRUNE, {})
    job = await job_queue.claim("a-worker")
    assert job is not None
    await registered_handlers()[jobs_module.SEARCH_EVENTS_PRUNE](
        JobContext(job=job, worker_id="a-worker", queue=job_queue)
    )

    assert await _kept(database) == ["ancient"]


def test_a_rule_that_is_not_a_number_falls_back_rather_than_deleting_everything() -> None:
    """A rule that is not a number falls back; a negative turns the sweep off."""
    assert jobs_module.keep_days_from(None) == jobs_module.DEFAULT_KEEP_DAYS
    assert jobs_module.keep_days_from("not a number") == jobs_module.DEFAULT_KEEP_DAYS
    assert jobs_module.keep_days_from(-30) == 0
    assert jobs_module.keep_days_from("90") == 90


async def test_a_saved_filter_compiles_to_the_walls_own_constraints_and_only_for_its_owner(
    database: Database,
) -> None:
    """A swap offering "everything this filter finds" reads the saved search through the same
    compiler the wall uses; another kind of saved search, another id, or another user gives None."""
    from sift.kernel.access import AssetFilter, Repository, Role
    from sift.kernel.config import get_settings
    from sift.kernel.content import ContentStore
    from sift.slices.search.filters import FilterCompiler
    from sift.slices.search.service import ASSET_WALL, SearchService
    from sift.testing.fixtures import create_user

    await _seed(database, 1)
    await index_assets(database, rebuild=True)
    access = Repository(database, ContentStore(database, get_settings()))
    service = SearchService(database, access, FilterCompiler(access))
    viewer = await create_user(database, Role.ADMIN)
    other = await create_user(database, Role.ADMIN)
    await service.save_search(viewer, "clips", "q=clip", ASSET_WALL)
    await service.save_search(viewer, "people", "q=clip", "person")
    kept = {one.name: one.id for one in await service.saved_searches(viewer)}

    found = await service.saved_filter(viewer, kept["clips"])

    assert isinstance(found, AssetFilter)
    assert await service.saved_filter(viewer, kept["people"]) is None
    assert await service.saved_filter(viewer, "01HX0000000000000000000000") is None
    assert await service.saved_filter(other, kept["clips"]) is None
