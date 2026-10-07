# SPDX-License-Identifier: AGPL-3.0-or-later
"""The search index is a rebuildable cache: a full rebuild from empty reproduces exactly what the
incremental writes produced, whatever order things happened in."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sift.kernel.db import Database
from sift.kernel.jobs import JobContext, JobQueue
from sift.slices.search.jobs import FTS_REINDEX, FTS_REINDEX_FILES
from sift.slices.search.tests.conftest import (
    EPOCH,
    World,
    db_path,
    found,
    read,
    reindex,
    sign_in,
    write,
)

pytestmark = [pytest.mark.integration]

_ROWS = "SELECT asset_id, filename, path, tags, people, usernames FROM assets_fts ORDER BY asset_id"


def _index(client: TestClient) -> list[dict[str, object]]:
    return read(db_path(client), _ROWS)


def test_a_full_rebuild_reproduces_the_identical_index(client: TestClient, world: World) -> None:
    """A rebuild is deterministic. The incremental path is compared with a rebuild in
    `test_reindexing_one_asset_at_a_time_gives_what_a_rebuild_would_have`."""
    before = _index(client)
    assert before, "the fixture did not build an index to compare against"

    reindex(db_path(client))

    assert _index(client) == before


def test_reindexing_one_asset_gives_what_a_rebuild_would_have_given(
    client: TestClient, world: World
) -> None:
    """The incremental path and the full path are one function, and this is why that matters."""
    write(db_path(client), [("DELETE FROM assets_fts", ())])
    for asset_id in (world.beach, world.walk, world.private, world.vaulted):
        reindex(db_path(client), asset_id=asset_id)
    incremental = _index(client)

    reindex(db_path(client))

    assert _index(client) == incremental


def test_reindexing_twice_does_not_duplicate_a_row(client: TestClient, world: World) -> None:
    """An FTS5 table carries no uniqueness of its own, so nothing but the delete-then-insert stops
    an asset matching twice and appearing twice in a result."""
    before = _index(client)
    reindex(db_path(client), asset_id=world.beach)
    reindex(db_path(client), asset_id=world.beach)
    assert _index(client) == before


def test_a_new_tag_becomes_searchable_after_the_asset_is_reindexed(
    client: TestClient, world: World
) -> None:
    """Free text has to agree with the tokens: a tag reachable by `tags:` must also be reachable
    by typing it into the box."""
    sign_in(client, "admin")
    assert found(client, q="sandcastle") == ([], 0)

    tag = "01HX0000000000000000000077"
    write(
        db_path(client),
        [
            (
                "INSERT INTO tags (id, name, created_at) VALUES (?, ?, ?)",
                (tag, "sandcastle", EPOCH),
            ),
            ("INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (world.beach, tag)),
        ],
    )
    reindex(db_path(client), asset_id=world.beach)

    assert found(client, q="sandcastle") == ([world.beach], 1)


def test_an_alias_is_indexed_alongside_the_name_it_stands_for(
    client: TestClient, world: World
) -> None:
    """An alias is indexed beside the name, so free text finds a person as `people:` does."""
    sign_in(client, "admin")
    people = read(
        db_path(client), "SELECT people FROM assets_fts WHERE asset_id = ?", (world.beach,)
    )
    assert "Jane Doe" in str(people[0]["people"])
    # `JD` is indexed though below the trigram floor: the floor is the query's.
    assert "JD" in str(people[0]["people"])


def test_a_removed_asset_stops_being_findable(client: TestClient, world: World) -> None:
    """A removed asset stops being findable: the sweep removes the FTS5 row, which nothing
    cascades."""
    sign_in(client, "admin")
    assert found(client, q="private_notes")[0] == [world.private]

    write(db_path(client), [("DELETE FROM assets WHERE id = ?", (world.private,))])
    reindex(db_path(client), asset_id=world.private)

    assert found(client, q="private_notes") == ([], 0)
    remaining = [row["asset_id"] for row in _index(client)]
    assert world.private not in remaining


def test_the_index_can_be_dropped_entirely_and_rebuilt(client: TestClient, world: World) -> None:
    """The property the whole design rests on: nothing is stored only here."""
    sign_in(client, "admin")
    write(db_path(client), [("DELETE FROM assets_fts", ())])
    assert found(client, q="beach_sunset") == ([], 0)

    written = reindex(db_path(client))

    assert written == 4
    assert found(client, q="beach_sunset") == ([world.beach], 1)


def test_a_file_with_nothing_on_it_is_still_findable_by_name(
    client: TestClient, world: World
) -> None:
    """No tags, nobody in it, no username, and those are the files most likely to be hunted for
    by name, because there is no other way to reach them. An inner join anywhere in the gathering
    query would drop exactly these."""
    sign_in(client, "admin")
    assert found(client, q="private_notes")[0] == [world.private]


async def test_an_initializer_at_its_current_version_does_nothing() -> None:
    """Told the schema is already where it should be, an initializer must not touch it."""
    from sift.slices.search import schema

    calls: list[str] = []

    class Recorder:
        async def execute(self, statement: str) -> None:  # pragma: no cover - see the assert
            calls.append(statement)

    await schema.initialize(Recorder(), on_disk=schema.VERSION)  # type: ignore[arg-type]
    assert calls == []


async def _run_job(path: Path, payload: dict[str, object], *, files: bool = False) -> list[str]:
    """Drive the handler the way a worker would, against a real database; the notes it left."""
    from sift.slices.search.jobs import reindex, reindex_files

    handler = reindex_files if files else reindex
    database = Database(path, readers=1)
    await database.connect()
    notes: list[str] = []
    try:
        queue = JobQueue(database)
        job_type = FTS_REINDEX_FILES if files else FTS_REINDEX
        job_id = await queue.enqueue(job_type, payload, require_handler=False)
        # Claimed past the work the application queued at boot.
        while True:
            job = await queue.claim("a-worker")
            assert job is not None, "the queue lost the job this test enqueued"
            if job.id == job_id:
                break
        said = queue.set_note

        async def noting(job_id: str, worker_id: str, note: str) -> bool:
            notes.append(note)
            return await said(job_id, worker_id, note)

        queue.set_note = noting  # type: ignore[method-assign]
        await handler(JobContext(job=job, worker_id="a-worker", queue=queue), database=database)
    finally:
        await database.close()
    return notes


@pytest.mark.parametrize("payload", [{}, {"asset_id": None}])
def test_the_job_rebuilds_the_whole_index_when_it_names_no_asset(
    client: TestClient, world: World, payload: dict[str, object]
) -> None:
    """Both ways of saying "all of them" mean the same thing to the handler."""
    before = _index(client)
    write(db_path(client), [("DELETE FROM assets_fts", ())])

    asyncio.run(_run_job(db_path(client), payload))

    assert _index(client) == before


def test_the_job_reindexes_one_asset_when_it_names_one(client: TestClient, world: World) -> None:
    """The incremental path, reached the way a tag change would reach it."""
    write(db_path(client), [("DELETE FROM assets_fts", ())])

    asyncio.run(_run_job(db_path(client), {"asset_id": world.beach}))

    assert [row["asset_id"] for row in _index(client)] == [world.beach]


def test_the_job_refuses_a_payload_that_is_not_an_asset_id(
    client: TestClient, world: World
) -> None:
    """A handler reached with the wrong payload was enqueued wrong, which is a bug rather than a
    runtime condition, so it raises, and the raise settles the job as failed with a reason a
    person can read instead of silently rebuilding the whole library."""
    with pytest.raises(ValueError, match="asset_id"):
        asyncio.run(_run_job(db_path(client), {"asset_id": 7}))


def test_the_handler_is_registered_under_the_name_the_queue_knows(
    client: TestClient,
) -> None:
    """The job type is a string on both sides of the queue, so nothing but this says the two
    agree. An unregistered type is refused at enqueue, which is where a typo would surface."""
    from sift.kernel.jobs import registered_handlers

    assert FTS_REINDEX in registered_handlers()


def test_a_rebuild_and_a_reindex_of_some_files_are_named_for_what_they_do(
    client: TestClient,
) -> None:
    """Activity reads the name: a rename's few files must not read as the whole index rebuilt."""
    from sift.kernel.jobs import registered_job_names

    names = registered_job_names()
    assert names[FTS_REINDEX] == "Recreating the search index"
    assert names[FTS_REINDEX_FILES] == "Updating the search index"


def test_the_job_reindexes_named_assets_a_chunk_at_a_time_and_tells_the_library(
    client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rename's job: exactly its files, one write per chunk, and LIBRARY said once at the end."""
    from sift.kernel import changes
    from sift.slices.search import jobs

    told: list[object] = []
    monkeypatch.setattr(jobs, "IDS_PER_WRITE", 1)
    monkeypatch.setattr(jobs, "announce", lambda _audience, about: told.append(about))
    written: list[list[str]] = []
    from sift.kernel.access import index_assets as real

    async def counting(database: Database, **kwargs: object) -> int:
        written.append(list(kwargs["asset_ids"]))  # type: ignore[call-overload]
        return await real(database, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(jobs, "index_assets", counting)
    write(db_path(client), [("DELETE FROM assets_fts", ())])

    notes = asyncio.run(
        _run_job(db_path(client), {"asset_ids": [world.beach, world.walk]}, files=True)
    )

    assert notes == ["For 2 files", "Indexed 2 files"]
    assert written == [[world.beach], [world.walk]]
    assert sorted(str(row["asset_id"]) for row in _index(client)) == sorted(
        [world.beach, world.walk]
    )
    assert told == [changes.About.LIBRARY]


@pytest.mark.parametrize("named", ["one", [7]])
def test_the_job_refuses_named_assets_that_are_not_ids(
    client: TestClient, world: World, named: object
) -> None:
    with pytest.raises(ValueError, match="asset_ids"):
        asyncio.run(_run_job(db_path(client), {"asset_ids": named}, files=True))


@pytest.mark.anyio
async def test_queue_many_queues_jobs_of_named_files_and_nothing_for_none() -> None:
    from sift.kernel.jobs.quiet_hours import AT_NOW
    from sift.slices.search import jobs
    from sift.slices.search.reindex import Reindexer

    asked: list[tuple[str, list[object], dict[str, object]]] = []

    class Queue:
        async def enqueue_many(self, job_type: str, payloads: list[object], **how: object) -> None:
            asked.append((job_type, payloads, how))

    seam = Reindexer(database=None, queue=Queue())  # type: ignore[arg-type]
    await seam.queue_many([])
    assert asked == []
    ids = [f"a{index}" for index in range(jobs.IDS_PER_JOB + 1)]
    await seam.queue_many(ids)
    ((job_type, payloads, how),) = asked
    assert job_type == FTS_REINDEX_FILES
    assert payloads == [{"asset_ids": ids[: jobs.IDS_PER_JOB]}, {"asset_ids": ids[-1:]}]
    assert how["at"] == AT_NOW


@pytest.mark.anyio
async def test_queue_many_logs_a_queue_that_refuses_and_raises_nothing() -> None:
    from sift.slices.search.reindex import Reindexer

    class Broken:
        async def enqueue_many(self, *_: object, **__: object) -> None:
            raise RuntimeError("the queue is unavailable")

    await Reindexer(database=None, queue=Broken()).queue_many(["a"])  # type: ignore[arg-type]


def _writes_to_the_index(client: TestClient, ids: list[str]) -> list[str]:
    """The statements a set-of-files job ran against the index's two tables, a chunk at a time."""
    from sift.kernel.db import StatementRun, statement_budget

    heard: list[StatementRun] = []
    statement_budget().heard = heard
    try:
        asyncio.run(_run_job(db_path(client), {"asset_ids": ids}, files=True))
    finally:
        statement_budget().heard = None
    return [run.name for run in heard if "assets_fts" in run.sql and run.stage != "db.read"]


def test_a_chunk_of_files_is_replaced_by_one_statement_per_table_and_swept_once(
    client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the index rows are written one by one; the deletes and the map rows are one statement a
    chunk whatever its size, and the orphan sweep runs once a job, never once a chunk."""
    from sift.slices.search import jobs

    monkeypatch.setattr(jobs, "PAUSE_BETWEEN_WRITES", 0)
    one = _writes_to_the_index(client, [world.beach])
    two = _writes_to_the_index(client, [world.beach, world.walk])
    inserts = [name for name in two if name.startswith("insert:assets_fts#")]
    assert len(two) - len(one) == 1 and len(inserts) == 2, f"{one} against {two}"

    monkeypatch.setattr(jobs, "IDS_PER_WRITE", 1)
    chunked = _writes_to_the_index(client, [world.beach, world.walk])
    sweeps = [name for name in chunked if name.startswith("delete:") and name not in two[:2]]
    # A second chunk adds its own delete, forget and map insert; the index inserts only move.
    assert len(chunked) == len(two) + 3, f"a second chunk cost more than its own writes: {chunked}"
    assert len(sweeps) == 2, f"the orphans were swept per chunk: {chunked}"


def test_the_job_pauses_after_each_chunk_so_a_waiting_write_goes_first(
    client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sift.kernel.access import index_assets as real_index
    from sift.slices.search import jobs

    order: list[str] = []
    real_sleep = asyncio.sleep

    async def chunk(database: Database, **kwargs: object) -> int:
        order.append("chunk")
        return await real_index(database, **kwargs)  # type: ignore[arg-type]

    async def pause(seconds: float) -> None:
        order.append(f"pause {seconds}")
        await real_sleep(0)

    monkeypatch.setattr(jobs, "IDS_PER_WRITE", 1)
    monkeypatch.setattr(jobs, "index_assets", chunk)
    monkeypatch.setattr(jobs, "sleep", pause)
    asyncio.run(_run_job(db_path(client), {"asset_ids": [world.beach, world.walk]}, files=True))

    wait = f"pause {jobs.PAUSE_BETWEEN_WRITES}"
    assert order == ["chunk", wait, "chunk", wait], order
    assert jobs.PAUSE_BETWEEN_WRITES > 0
