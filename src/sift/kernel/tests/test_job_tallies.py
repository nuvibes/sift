# SPDX-License-Identifier: AGPL-3.0-or-later
"""The queue's tallies are kept by the jobs table's triggers, and the dashboard's numbers read them
and the current run's rows, never the week of settled rows behind them."""

from __future__ import annotations

from typing import Any

import pytest

from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.jobs import JobQueue, queue
from sift.kernel.jobs import schema as jobs_schema
from sift.kernel.jobs.queue_rows import WorkKind
from sift.kernel.tests.jobs_helpers import noop_handler
from sift.testing.fixtures import FakeClock

pytestmark = pytest.mark.usefixtures("clean_handlers")

#: What the dashboard read before the tallies, kept as the oracle the new reads must agree with.
_WHOLE_TABLE = """
SELECT type, state, COUNT(*) AS n, SUM(created_at >= ?) AS in_run,
       SUM(updated_at >= ?) AS lately, SUM(units * (1 - progress)) AS left_units
  FROM jobs GROUP BY type, state
"""

_COUNTED = "SELECT type, state, COUNT(*) AS n FROM jobs GROUP BY type, state"


async def _tallies(database: Database) -> dict[tuple[str, str], int]:
    rows = await database.fetch_all(queue._TALLIES)
    return {(str(row["type"]), str(row["state"])): int(row["n"]) for row in rows}


async def _counted(database: Database) -> dict[tuple[str, str], int]:
    rows = await database.fetch_all(_COUNTED)
    return {(str(row["type"]), str(row["state"])): int(row["n"]) for row in rows}


async def _a_mixed_queue(job_queue: JobQueue, probe: str = "probe", scan: str = "scan") -> None:
    for n in range(4):
        await job_queue.enqueue(probe, {"n": n})
    await job_queue.enqueue(scan, {"n": 9})
    worker = new_id()
    for _ in range(3):
        claimed = await job_queue.claim(worker)
        assert claimed is not None
        if claimed.payload["n"] == 1:
            await job_queue.fail(claimed.id, worker, "unreadable", permanent=True)
        elif claimed.payload["n"] == 2:
            continue
        else:
            await job_queue.complete(claimed.id, worker)
    waiting = await job_queue.enqueue(probe, {"n": 7})
    await job_queue.cancel(waiting)


async def test_the_tallies_follow_every_insert_move_and_removal(job_queue: JobQueue) -> None:
    database = job_queue._db
    noop_handler("probe"), noop_handler("scan")
    await _a_mixed_queue(job_queue)
    assert await _tallies(database) == await _counted(database)

    async with database.write() as connection:
        await connection.execute("UPDATE jobs SET type = 'scan' WHERE type = 'probe'")
        await connection.execute("DELETE FROM jobs WHERE state = 'done'")
    counted = await _counted(database)
    assert await _tallies(database) == counted
    assert await job_queue.counts() == {state: n for (_, state), n in counted.items()}
    assert await job_queue.counts_by_type() == {"scan": await job_queue.counts()}


async def test_the_summary_agrees_with_a_count_of_every_row(temp_db: Database) -> None:
    clock = FakeClock(1_000_000)
    await temp_db.initialize_schema()
    job_queue = JobQueue(temp_db, clock=clock.now, summary_fresh_for=0)
    noop_handler("probe"), noop_handler("scan")
    await _a_mixed_queue(job_queue)
    clock.advance(600)
    await _a_mixed_queue(job_queue)

    summary = await job_queue.work_summary()
    now = int(clock.now())
    rows = await temp_db.fetch_all(_WHOLE_TABLE, (summary.since, now - 120))
    states: dict[str, dict[str, int]] = {}
    run: dict[str, WorkKind] = {}
    for row in rows:
        kind, state = str(row["type"]), str(row["state"])
        states.setdefault(kind, {})[state] = int(row["n"])
        here = run.setdefault(kind, WorkKind())
        if state in ("queued", "running", "blocked", "paused"):
            here.outstanding += int(row["in_run"] or 0)
            here.left_units += float(row["left_units"] or 0)
        elif state == "done":
            here.done += int(row["in_run"] or 0)
            here.per_minute = round(int(row["lately"] or 0) * 60 / 120, 2)
        elif state == "failed":
            here.failed += int(row["in_run"] or 0)

    assert summary.since is not None
    assert summary.states == states
    assert summary.run == run


#: A week of settled rows and a few live ones, the shape the statistics describe on a real queue.
_A_WEEK_SETTLED = """
WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 3000)
INSERT INTO jobs (id, type, state, payload, created_at, updated_at, root_id)
SELECT printf('J%05d', i), CASE i % 3 WHEN 0 THEN 'probe' WHEN 1 THEN 'scan' ELSE 'thumbnail' END,
       CASE WHEN i % 50 = 0 THEN 'queued' WHEN i % 7 = 0 THEN 'failed' ELSE 'done' END,
       '{}', i, i, printf('J%05d', i)
  FROM n
"""


async def test_the_dashboards_reads_never_walk_the_settled_rows(job_queue: JobQueue) -> None:
    async with job_queue._db.write() as connection:
        await connection.execute(_A_WEEK_SETTLED)
        await connection.execute("ANALYZE")
    for statement, params, index in (
        (queue._TALLIES, (), None),
        (queue._LIVE_SUMMARY, (0,), None),
        (queue._SETTLED_IN_RUN, (2990,), "ix_jobs_settled_by_created"),
        (queue._DONE_LATELY, (2990,), "ix_jobs_done_by_updated"),
    ):
        plan = await job_queue._db.fetch_all(
            "EXPLAIN QUERY PLAN " + statement,  # nosemgrep: sift-no-string-built-sql
            params,
        )
        steps = [str(row["detail"]) for row in plan]
        assert not [step for step in steps if step.startswith("SCAN jobs")], steps
        assert index is None or any(index in step for step in steps), steps


@pytest.mark.integration
async def test_a_queue_from_before_the_tallies_is_counted_into_them(temp_db: Database) -> None:
    """Version 16: the tallies are counted from the rows a library already holds, then kept."""
    async with temp_db.write() as connection:
        await connection.execute("DROP TABLE IF EXISTS jobs")
        await connection.execute("DROP TABLE IF EXISTS job_tallies")
        await connection.execute(jobs_schema._CREATE_TABLE)
        for job_id, state in (("J1", "done"), ("J2", "done"), ("J3", "queued")):
            await connection.execute(
                "INSERT INTO jobs (id, type, state, payload, created_at, updated_at, root_id)"
                " VALUES (?, 'probe', ?, '{}', 1, 1, ?)",
                (job_id, state, job_id),
            )
        await jobs_schema.initialize(connection, on_disk=15)
        await connection.execute("UPDATE jobs SET state = 'running' WHERE id = 'J3'")

    assert await _tallies(temp_db) == {("probe", "done"): 2, ("probe", "running"): 1}


async def test_a_list_filtered_by_state_and_type_alone_takes_its_total_from_the_tallies(
    job_queue: JobQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sift.kernel.jobs import JobState, queue_pages

    noop_handler("probe"), noop_handler("scan")
    await _a_mixed_queue(job_queue)
    counted = await _counted(job_queue._db)

    def walked(_params: object) -> str:
        raise AssertionError("the total walked the table")

    monkeypatch.setattr(queue_pages, "_list_total", walked)
    assert (await job_queue.list()).total == sum(counted.values())
    assert (await job_queue.list(state=JobState.DONE)).total == sum(
        n for (_, state), n in counted.items() if state == "done"
    )
    assert (await job_queue.list(job_type="probe", leaving_out=["scan"])).total == sum(
        n for (kind, _), n in counted.items() if kind == "probe"
    )
    assert (await job_queue.list(leaving_out=["scan"])).total == sum(
        n for (kind, _), n in counted.items() if kind != "scan"
    )


_FAMILY_COUNTED = (
    "SELECT COALESCE(root_id, id) AS root_id, state, type, COALESCE(timing, '') AS timing,"
    " COUNT(*) AS n FROM jobs GROUP BY 1, 2, 3, 4"
)


async def _family_tallies(database: Database) -> dict[tuple[str, str, str, str], int]:
    rows = await database.fetch_all("SELECT * FROM job_family_tallies")
    return {(row["root_id"], row["state"], row["type"], row["timing"]): row["n"] for row in rows}


async def _family_counted(database: Database) -> dict[tuple[str, str, str, str], int]:
    rows = await database.fetch_all(_FAMILY_COUNTED)
    return {(row["root_id"], row["state"], row["type"], row["timing"]): row["n"] for row in rows}


async def test_the_family_tallies_follow_every_insert_move_and_removal(
    job_queue: JobQueue,
) -> None:
    database = job_queue._db
    noop_handler("probe"), noop_handler("scan")
    await _a_mixed_queue(job_queue)
    top = await job_queue.enqueue("scan", {"n": 20})
    for n in range(3):
        await job_queue.enqueue("probe", {"n": 30 + n}, parent_id=top)
    assert await _family_tallies(database) == await _family_counted(database)

    async with database.write() as connection:
        await connection.execute("UPDATE jobs SET timing = 'now' WHERE parent_id = ?", (top,))
        await connection.execute("UPDATE jobs SET type = 'scan' WHERE type = 'probe'")
        await connection.execute("DELETE FROM jobs WHERE state = 'done'")
    assert await _family_tallies(database) == await _family_counted(database)

    async with database.write() as connection:
        await connection.execute("DELETE FROM jobs")
    assert await _family_tallies(database) == {}, "a family gone leaves no row behind"


@pytest.mark.integration
async def test_a_queue_from_before_the_family_tallies_is_counted_into_them(
    temp_db: Database,
) -> None:
    """Version 17: the family tallies are counted from the rows a library already holds, then kept."""
    async with temp_db.write() as connection:
        await connection.execute("DROP TABLE IF EXISTS jobs")
        await connection.execute("DROP TABLE IF EXISTS job_family_tallies")
        await connection.execute(jobs_schema._CREATE_TABLE)
        for job_id, state, root in (
            ("J1", "done", "J1"),
            ("J2", "queued", "J1"),
            ("J3", "queued", None),
        ):
            await connection.execute(
                "INSERT INTO jobs (id, type, state, payload, created_at, updated_at, root_id, timing)"
                " VALUES (?, 'probe', ?, '{}', 1, 1, ?, 'quiet')",
                (job_id, state, root),
            )
        await jobs_schema.initialize(connection, on_disk=16)
        await connection.execute("UPDATE jobs SET state = 'running' WHERE id = 'J2'")

    assert await _family_tallies(temp_db) == {
        ("J1", "done", "probe", "quiet"): 1,
        ("J1", "running", "probe", "quiet"): 1,
        ("J3", "queued", "probe", "quiet"): 1,
    }


# The pool's three reads as they were, over every row: the oracle the tallied reads must agree with.
_DEMAND_OVER_ROWS = """
SELECT type, COUNT(*) AS pending FROM jobs
 WHERE state IN ('queued', 'running', 'blocked')
   AND NOT (state = 'queued' AND NOT ?
            AND (COALESCE(timing, '') = 'quiet'
                 OR (timing IS NULL AND type IN (SELECT value FROM json_each(?)))))
   AND NOT (state = 'queued' AND NOT ?
            AND root_id IN (SELECT value FROM json_each(?)) AND COALESCE(timing, '') <> 'now'
            AND type NOT IN (SELECT value FROM json_each(?)))
 GROUP BY type
"""

_HELD_OVER_ROWS = """
SELECT root_id, type, COUNT(*) AS held FROM jobs
 WHERE root_id IN (SELECT value FROM json_each(:families)) AND state = 'queued'
   AND (run_after IS NULL OR run_after <= :now)
   AND COALESCE(timing, '') <> 'now'
   AND type NOT IN (SELECT value FROM json_each(:spared))
 GROUP BY root_id, type
"""

_DUE_OVER_ROWS = """
SELECT type, COUNT(*) AS pending FROM jobs
 WHERE state IN ('queued', 'running', 'blocked')
   AND NOT (state = 'queued' AND run_after IS NOT NULL AND run_after > :now)
   AND (:everything OR state = 'running' OR type IN (SELECT value FROM json_each(:alone)))
 GROUP BY type
"""

#: Every kind, family, timing, state and wait together: 270 rows, each combination once.
_EVERY_SHAPE = """
WITH kinds(k) AS (VALUES ('a'), ('b'), ('c')),
     tops(t) AS (VALUES ('X'), ('Y')),
     timings(m) AS (VALUES (NULL), ('now'), ('quiet')),
     states(s) AS (VALUES ('queued'), ('running'), ('blocked'), ('paused'), ('done')),
     waits(w) AS (VALUES (NULL), (500), (5000))
INSERT INTO jobs (id, type, state, payload, created_at, updated_at, root_id, timing, run_after)
SELECT printf('%s-%s-%s-%s-%s', t, k, COALESCE(m, '-'), s, COALESCE(w, 0)), k, s, '{}', 1, 1, t, m, w
  FROM kinds, tops, timings, states, waits
"""


def _keyed(rows: list[Any], *key: str, value: str) -> dict[tuple[Any, ...], Any]:
    return {tuple(row[name] for name in key): row[value] for row in rows}


async def test_the_pools_reads_from_the_tallies_agree_with_a_count_of_every_row(
    job_queue: JobQueue,
) -> None:
    from sift.kernel.jobs import queue_reads, queue_switchboard

    database = job_queue._db
    async with database.write() as connection:
        await connection.execute(_EVERY_SHAPE)
    for quiet_open in (True, False):
        for quiet_types in ("[]", '["a"]'):
            for holds in (
                (True, "[]", "[]"),
                (False, '["X"]', '["b"]'),
                (False, '["X", "Y"]', "[]"),
            ):
                args = (quiet_open, quiet_types, *holds)
                tallied = await database.fetch_all(queue_switchboard._DEMAND_BY_TYPE, args)
                counted = await database.fetch_all(_DEMAND_OVER_ROWS, args)
                assert _keyed(tallied, "type", value="pending") == _keyed(
                    counted, "type", value="pending"
                ), args
    for now in (0, 1000, 10_000):
        for families, spared in (('["X"]', "[]"), ('["X", "Y"]', '["b"]'), ("[]", "[]")):
            held = {"families": families, "now": now, "spared": spared}
            tallied = await database.fetch_all(queue_switchboard._HELD_FOR_A_FAMILY, held)
            counted = await database.fetch_all(_HELD_OVER_ROWS, held)
            assert _keyed(tallied, "root_id", "type", value="held") == _keyed(
                counted, "root_id", "type", value="held"
            ), held
        for everything, alone in ((True, "[]"), (False, '["c"]'), (False, "[]")):
            args2 = {"now": now, "everything": everything, "alone": alone}
            tallied = await database.fetch_all(queue_reads._DUE_BY_TYPE, args2)
            counted = await database.fetch_all(_DUE_OVER_ROWS, args2)
            assert _keyed(tallied, "type", value="pending") == _keyed(
                counted, "type", value="pending"
            ), args2


async def test_the_pools_reads_never_walk_the_queue(job_queue: JobQueue) -> None:
    from sift.kernel.jobs import queue_reads, queue_switchboard

    async with job_queue._db.write() as connection:
        await connection.execute(_A_WEEK_SETTLED)
        await connection.execute("ANALYZE")
    for statement, params in (
        (queue_switchboard._DEMAND_BY_TYPE, (False, '["a"]', False, '["J00050"]', '["b"]')),
        (
            queue_switchboard._HELD_FOR_A_FAMILY,
            {"families": '["J00050"]', "now": 0, "spared": "[]"},
        ),
        (queue_reads._DUE_BY_TYPE, {"now": 0, "everything": True, "alone": "[]"}),
        (queue_reads._UNFINISHED_BY_TYPE, ()),
        (queue_reads._LIVE_BY_TYPE, ()),
        (queue_reads._OUTSTANDING, ('["probe"]',)),
    ):
        plan = await job_queue._db.fetch_all(
            "EXPLAIN QUERY PLAN " + statement,  # nosemgrep: sift-no-string-built-sql
            params,
        )
        steps = [str(row["detail"]) for row in plan]
        assert not [step for step in steps if step.startswith("SCAN jobs")], steps
        assert not [
            step for step in steps if "jobs USING" in step and "queued_later" not in step
        ], steps
