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
from sift.slices.search.jobs import FTS_REINDEX
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


async def _run_job(path: Path, payload: dict[str, object]) -> None:
    """Drive the handler the way a worker would, against a real database."""
    from sift.slices.search.jobs import reindex as handler

    database = Database(path, readers=1)
    await database.connect()
    try:
        queue = JobQueue(database)
        job_id = await queue.enqueue(FTS_REINDEX, payload, require_handler=False)
        # Claimed past the work the application queued at boot.
        while True:
            job = await queue.claim("a-worker")
            assert job is not None, "the queue lost the job this test enqueued"
            if job.id == job_id:
                break
        await handler(JobContext(job=job, worker_id="a-worker", queue=queue), database=database)
    finally:
        await database.close()


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
