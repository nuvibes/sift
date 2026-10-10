# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the job queue.

Every one of these runs against a real SQLite file in WAL mode. The failures this module can have
(two workers claiming the same job, a job silently lost to a restart, a hung worker's job redone
while it is still running it) are all concurrency and all durability, and neither reproduces
against a fake.

The pieces that matter most are also mutation-tested: the claim, the fence that stops a worker
finishing a job it no longer owns, the attempt counter, and the reclaim. A passing test proves
nothing about a guard until the guard has been removed and the test has been watched to fail.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel (the error class only)
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from sift.kernel import changes
from sift.kernel.changes import About, ChangeBus
from sift.kernel.db import Database, registered_components
from sift.kernel.jobs import (
    JobContext,
    JobQueue,
    JobState,
    UnknownJobType,
    WorkerPool,
    by_itself_job_types,
    job_name,
    register_handler,
    registered_handlers,
    registered_job_names,
)
from sift.kernel.jobs import schema as jobs_schema
from sift.kernel.jobs.queue import _check_payload, _for_the_record
from sift.kernel.jobs.queue_enqueue import SETTLE_SLACK_SECONDS
from sift.kernel.jobs.quiet_hours import AT_NOW
from sift.kernel.jobs.switchboard import JobSwitchedOff, Switch
from sift.kernel.jobs.tuning import (
    MAX_ATTEMPTS_CEILING,
    PRIORITY_MAX,
    PRIORITY_MIN,
    SETTLE_LONGEST_SECONDS,
)
from sift.kernel.tests.jobs_helpers import (
    OTHER_WORKER,
    WORKER,
    noop_handler,
)
from sift.testing.fixtures import FakeClock

pytestmark = pytest.mark.usefixtures("clean_handlers")


# --- what a job is called ----------------------------------------------------------------
#
# The name is declared beside the handler and travels out with the job, because the screen does not
# own the work: a map on the client would know some types and show the internal word for the
# rest. Making the name required is what turns "every job has a name" from something
# somebody checks into something that cannot be omitted, so the refusal itself is worth a test.


@pytest.mark.unit
def test_a_handler_with_no_name_is_refused() -> None:
    """Refused at the declaration, which means at startup rather than at review.

    A handler registered with a blank name would put `compress_sample` on the queue screen, and
    nothing downstream can tell that apart from a name somebody chose.
    """

    async def handler(context: JobContext) -> None:
        return None

    with pytest.raises(ValueError, match="needs a name"):
        register_handler("probe", handler, name="   ")

    assert "probe" not in registered_handlers()


@pytest.mark.unit
def test_registering_the_same_job_type_twice_is_refused() -> None:
    """Two features claiming one type is a bug, and the second must not quietly win."""
    noop_handler("probe")

    async def other(context: JobContext) -> None:
        return None

    with pytest.raises(ValueError, match="already registered"):
        register_handler("probe", other, name="Something else")


@pytest.mark.unit
def test_work_that_runs_by_itself_is_declared_beside_its_handler() -> None:
    """Read back through `by_itself_job_types`, and never also upkeep that is not listed at all:
    a type is either off Activity whole or listed inside the run that holds it."""

    async def handler(context: JobContext) -> None:
        return None

    register_handler("probe", handler, name="Probing file", by_itself=True)
    register_handler("scan", handler, name="Scanning folder")

    assert "probe" in by_itself_job_types()
    assert "scan" not in by_itself_job_types()
    with pytest.raises(ValueError, match="not both"):
        register_handler("backup_run", handler, name="Backing up", unlisted=True, by_itself=True)
    assert "backup_run" not in registered_handlers()


@pytest.mark.unit
def test_every_claimed_type_can_be_read_back_with_its_name() -> None:
    noop_handler("probe")
    noop_handler("scan")

    names = registered_job_names()

    assert names["probe"] == "Test job"
    assert names["scan"] == "Test job"


@pytest.mark.unit
def test_a_type_nothing_has_claimed_answers_with_its_own_name() -> None:
    """A row left in the queue by a version that had a job this one does not.

    The work cannot run either way. A blank line would say less about that than the type does, so
    the type is what the row shows.
    """
    noop_handler("probe")

    assert job_name("probe") == "Test job"
    assert job_name("something_a_later_version_had") == "something_a_later_version_had"


# --- the schema -------------------------------------------------------------------------


@pytest.mark.unit
def test_the_state_column_accepts_exactly_the_states_that_exist() -> None:
    """The CHECK constraint and `JobState` are two lists of the same thing.

    SQLite will not take a placeholder in a CHECK, and building the constraint from the enum would
    mean assembling SQL at runtime. So they are written out twice and kept honest here, because
    the way this drifts is that someone adds a state to the enum, every test passes, and the first
    row that uses it fails to write on a user's machine.
    """
    constraint = re.search(r"CHECK\(state IN \(([^)]+)\)\)", jobs_schema._CREATE_TABLE)
    assert constraint is not None
    in_sql = {value.strip().strip("'") for value in constraint.group(1).split(",")}

    assert in_sql == {state.value for state in JobState}


@pytest.mark.unit
def test_the_jobs_table_is_registered_with_the_kernel() -> None:
    assert "jobs" in registered_components()


@pytest.mark.integration
async def test_the_table_has_the_indexes_its_queries_need(job_queue: JobQueue) -> None:
    """A missing index here is not a slow query, it is a full scan under the write lock."""
    rows = await job_queue._db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'jobs'"
    )
    names = {row["name"] for row in rows}

    assert {
        "ix_jobs_claim_by_id",
        "ix_jobs_watchdog",
        "ix_jobs_parent",
        # The one the SCREEN reads a state by. Without it, listing fifty jobs reads and sorts the
        # whole table, once a second while the queue is open. The unfiltered list walks the
        # primary key's own index, newest id first.
        "ix_jobs_state_by_id",
        "ix_jobs_family_by_id",
        "ix_jobs_tops_by_id",
    } <= names


@pytest.mark.integration
async def test_initializing_twice_changes_nothing(job_queue: JobQueue) -> None:
    job_id = await job_queue.enqueue(noop_handler(), {"asset_id": "A"})

    await job_queue._db.initialize_schema()

    job = await job_queue.get(job_id)
    assert job is not None


@pytest.mark.integration
async def test_the_table_is_not_recreated_over_a_database_that_already_has_it(
    job_queue: JobQueue,
) -> None:
    """The initializer is told what version is on disk, and does nothing when it is already there.

    Called directly, because the kernel would not call it at all in this case, and a `CREATE
    TABLE` that ran anyway against a future version of this table is how data gets destroyed.
    """
    job_id = await job_queue.enqueue(noop_handler(), {"asset_id": "A"})

    async with job_queue._db.write() as connection:
        await jobs_schema.initialize(connection, on_disk=jobs_schema.VERSION)

    job = await job_queue.get(job_id)
    assert job is not None


@pytest.mark.integration
async def test_a_fresh_queue_does_not_also_run_the_migration(temp_db: Database) -> None:
    """A fresh database runs the CREATE and nothing else.

    The steps are independent `if`s so that a database several versions behind gets all of them in
    one boot, which means each step has to decide for itself whether it applies. This one does not,
    on a fresh install: the CREATE has just written the column, and adding it again is an error.
    """
    async with temp_db.write() as connection:
        await connection.execute("DROP TABLE IF EXISTS jobs")
        await jobs_schema.initialize(connection, on_disk=0)

    columns = await temp_db.fetch_all("SELECT name FROM pragma_table_info('jobs')")
    assert [row["name"] for row in columns].count("run_after") == 1


@pytest.mark.integration
async def test_version_20_lets_a_stored_stash_box_key_go(job_queue: JobQueue) -> None:
    """A master key carried in an enrich payload before round P is dropped by the step, and the
    rest of the payload is kept; a payload with no key is left as it is."""
    handler = noop_handler()
    keyed = await job_queue.enqueue(handler, {"asset_id": "A"})
    plain = await job_queue.enqueue(handler, {"asset_id": "B"})
    async with job_queue._db.write() as connection:
        await connection.execute(
            "UPDATE jobs SET type = 'stash_box_enrich', payload = json_set(payload, '$.key', 'k')"
            " WHERE id = ?",
            (keyed,),
        )
        await connection.execute("UPDATE jobs SET type = 'stash_box_enrich' WHERE id = ?", (plain,))
        await jobs_schema.initialize(connection, on_disk=19)

    rows = {
        row["id"]: json.loads(row["payload"])
        for row in await job_queue._db.fetch_all(
            "SELECT id, payload FROM jobs WHERE id IN (?, ?)", (keyed, plain)
        )
    }
    assert rows[keyed] == {"asset_id": "A"}
    assert rows[plain] == {"asset_id": "B"}


# --- enqueue ----------------------------------------------------------------------------


@pytest.mark.integration
async def test_an_enqueued_job_is_queued_and_readable(job_queue: JobQueue) -> None:
    job_id = await job_queue.enqueue(noop_handler(), {"asset_id": "01HQ"})

    job = await job_queue.get(job_id)

    assert job is not None
    assert job.state is JobState.QUEUED
    assert job.type == "probe"
    assert job.payload == {"asset_id": "01HQ"}
    assert job.attempts == 0
    assert job.progress == 0


@pytest.mark.integration
async def test_a_job_with_no_payload_is_fine(job_queue: JobQueue) -> None:
    job_id = await job_queue.enqueue(noop_handler("vacuum"))

    job = await job_queue.get(job_id)

    assert job is not None
    assert job.payload == {}


@pytest.mark.integration
async def test_a_deduped_job_collapses_onto_the_one_already_waiting(job_queue: JobQueue) -> None:
    """Work that names a PLACE rather than a moment is worth doing once.

    This is the guard on the runaway a watcher can cause. A filesystem whose change events never
    settle (a network share, or a Windows drive seen through WSL) asks for the same folder to be
    scanned every few seconds, and without this every ask is another job, until every worker is
    busy re-scanning one folder and nothing else in the queue ever runs.
    """
    scan = noop_handler()
    first = await job_queue.enqueue(scan, {"folder_id": "01HQ"}, dedupe=True)
    second = await job_queue.enqueue(scan, {"folder_id": "01HQ"}, dedupe=True)

    assert second == first, "the second ask collapsed onto the job already waiting"


@pytest.mark.integration
async def test_a_callers_row_is_written_with_its_job_or_not_at_all(job_queue: JobQueue) -> None:
    """A row naming its job, written in the job's own transaction: a stop leaves both or neither,
    and a collapsed ask joins the job that was already waiting."""
    await job_queue._db.execute("CREATE TABLE asked (job_id TEXT NOT NULL, what TEXT UNIQUE)")
    scan = noop_handler()
    row = "INSERT INTO asked (job_id, what) VALUES (?, ?)"

    first = await job_queue.enqueue(scan, {"folder_id": "01HQ"}, with_row=(row, ("one",)))
    collapsed = await job_queue.enqueue(
        scan, {"folder_id": "01HR"}, dedupe=True, with_row=(row, ("two",))
    )
    again = await job_queue.enqueue(
        scan, {"folder_id": "01HR"}, dedupe=True, with_row=(row, ("three",))
    )
    with pytest.raises(sqlite3.IntegrityError):
        await job_queue.enqueue(scan, {"folder_id": "01HS"}, with_row=(row, ("one",)))

    asked = await job_queue._db.fetch_all("SELECT job_id, what FROM asked ORDER BY what")
    assert [(r["what"], r["job_id"]) for r in asked] == [
        ("one", first),
        ("three", collapsed),
        ("two", collapsed),
    ]
    assert again == collapsed
    jobs = await job_queue._db.fetch_all("SELECT payload FROM jobs WHERE payload LIKE '%01HS%'")
    assert jobs == [], "the refused row took its job with it"


@pytest.mark.integration
async def test_a_job_keeps_the_user_that_requested_it_and_nobody_is_none(
    job_queue: JobQueue,
) -> None:
    """`requested_by` is written with the row and comes back on the claim, where the pool hands it
    to the ledger; a job nobody pressed for says nobody rather than anything else."""
    scan = noop_handler()
    pressed = await job_queue.enqueue(scan, {"folder_id": "01HQ"}, requested_by="acct-1")
    arrived = await job_queue.enqueue(scan, {"folder_id": "01HR"})

    first = await job_queue.claim("worker-1")
    second = await job_queue.claim("worker-1")

    assert first is not None and first.id == pressed and first.requested_by == "acct-1"
    assert second is not None and second.id == arrived and second.requested_by is None


@pytest.mark.integration
async def test_a_press_collapsing_onto_a_waiting_job_is_requested_by_the_person(
    job_queue: JobQueue,
) -> None:
    """A press that dedupes onto a row a watcher queued is still the person's press, so the row
    is recorded as theirs; a later press does not take the row off the first person."""
    scan = noop_handler()
    watched = await job_queue.enqueue(scan, {"folder_id": "01HQ"}, dedupe=True)
    pressed = await job_queue.enqueue(
        scan, {"folder_id": "01HQ"}, dedupe=True, requested_by="acct-1"
    )
    again = await job_queue.enqueue(scan, {"folder_id": "01HQ"}, dedupe=True, requested_by="acct-2")

    assert watched == pressed == again
    job = await job_queue.get(watched)
    assert job is not None and job.requested_by == "acct-1"


@pytest.mark.integration
async def test_a_batch_is_one_write_and_collapses_as_one_enqueue_does(
    job_queue: JobQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`enqueue_many` is ONE write transaction, and still dedupes row by row.

    One `enqueue` per file is one commit per file, and a press over a thousand files would answer
    only after a long wait on a busy queue. The batch must not buy that back by collapsing less: a
    row identical to one already waiting, or to one written earlier in the same batch, lands on
    that row.
    """
    scan = noop_handler()
    waiting = await job_queue.enqueue(scan, {"folder_id": "01HQ"}, dedupe=True)
    opened = 0
    real = job_queue._db.write

    @asynccontextmanager
    async def counted() -> AsyncIterator[object]:
        nonlocal opened
        opened += 1
        async with real() as connection:
            yield connection

    monkeypatch.setattr(job_queue._db, "write", counted)
    payloads = [{"folder_id": f"01H{index}"} for index in range(40)]
    placed = await job_queue.enqueue_many(
        scan,
        [*payloads, {"folder_id": "01HQ"}, {"folder_id": "01H0"}],
        dedupe=True,
        requested_by="acct-1",
    )

    assert opened == 1, "forty-two rows, one transaction"
    assert placed[40] == waiting, "onto the job already waiting"
    assert placed[41] == placed[0], "onto a row the same batch wrote"
    assert len(set(placed)) == 41
    job = await job_queue.get(placed[0])
    assert job is not None and job.requested_by == "acct-1"

    opened = 0
    plain = await job_queue.enqueue_many(scan, [{"n": 1}, {"n": 1}, {"n": 2}])
    assert opened == 1 and len(set(plain)) == 3, "without dedupe every payload is its own job"


@pytest.mark.integration
async def test_a_job_queued_without_a_collapse_is_told_to_the_dashboard(
    job_queue: JobQueue,
) -> None:
    """Activity is drawn when it is told the queue moved, not on a timer, so a queued
    job nobody announces is a row missing from it until something else in the queue happens to
    move. Both ways of queueing without `dedupe` are held to announcing."""
    bus = ChangeBus()
    changes.listens(bus)
    try:
        watching = bus.subscribe("admin")
        scan = noop_handler()

        await job_queue.enqueue(scan, {"folder_id": "01HQ"})
        assert watching.take(as_admin=True).about == (About.JOBS,), "one job, queued"

        await job_queue.enqueue_many(scan, [{"n": 1}, {"n": 2}])
        assert watching.take(as_admin=True).about == (About.JOBS,), "a batch, queued"
    finally:
        changes.listens(None)


@pytest.mark.integration
async def test_retiming_a_row_that_is_not_waiting_moves_nothing(job_queue: JobQueue) -> None:
    """A schedule placed afresh moves the one waiting run to its new moment. A run that is no
    longer waiting (claimed, done, or never there) is left alone, and the answer says so."""
    assert await job_queue.retime_waiting("01ARROWNOSUCHJOB0000000000", 5) is False


async def test_dedupe_looks_only_at_what_is_still_waiting(job_queue: JobQueue) -> None:
    """A job already running may have walked past the change that prompted the new ask, so
    collapsing onto it would lose that change. One running and one waiting is the most it holds."""
    scan = noop_handler()
    first = await job_queue.enqueue(scan, {"folder_id": "01HQ"}, dedupe=True)
    claimed = await job_queue.claim("worker-1")
    assert claimed is not None and claimed.id == first

    second = await job_queue.enqueue(scan, {"folder_id": "01HQ"}, dedupe=True)

    assert second != first, "a job already under way is not something to collapse onto"


@pytest.mark.integration
async def test_dedupe_tells_two_different_payloads_apart(job_queue: JobQueue) -> None:
    """Two folders are two jobs. Collapsing on the type alone would scan one and call it both."""
    scan = noop_handler()
    first = await job_queue.enqueue(scan, {"folder_id": "01HQ"}, dedupe=True)
    other = await job_queue.enqueue(scan, {"folder_id": "01HR"}, dedupe=True)

    assert other != first


@pytest.mark.integration
async def test_work_asked_for_when_settled_waits_and_collapses(job_queue: JobQueue) -> None:
    """The two properties whole-library follow-up work depends on, asserted together.

    Neither is enough alone. Without the wait, the first file of an import runs the sweep against a
    library that is still filling; without the collapse, a thousand files queue a thousand sweeps.
    Two features rely on this: grouping unclaimed faces, and comparing fingerprints for near
    duplicates.
    """
    sweep = noop_handler()

    first = await job_queue.enqueue_when_settled(sweep)
    again = await job_queue.enqueue_when_settled(sweep)

    assert again == first, "the second ask collapsed onto the one already waiting"
    assert await job_queue.claim("worker-1") is None, "it must not be claimable before it settles"

    job = await job_queue.get(first)
    assert job is not None and job.run_after is not None


@pytest.mark.integration
async def test_a_settle_runs_a_minute_after_the_last_file_not_the_first(
    temp_db: Database,
) -> None:
    """On a first import, a sweep falling due a minute after the FIRST file would run while the
    last reads were still writing their fingerprints, and run again a minute after them. Each file
    collapsing onto the waiting request puts it off to a minute after itself, and
    no further than the longest a settle waits, or a folder that never stops would never be swept."""
    await temp_db.initialize_schema()
    clock = FakeClock(1000)
    queue = JobQueue(temp_db, clock=clock.now)
    sweep = noop_handler()

    first = await queue.enqueue_when_settled(sweep)
    clock.advance(50)
    assert await queue.enqueue_when_settled(sweep) == first

    clock.advance(20)  # a minute after the first ask, ten seconds after the second
    assert await queue.claim(WORKER) is None, "put off to a minute after the last file"

    for _ in range(SETTLE_LONGEST_SECONDS // 30):
        clock.advance(30)
        await queue.enqueue_when_settled(sweep)
    job = await queue.get(first)
    assert job is not None and job.run_after == 1000 + SETTLE_LONGEST_SECONDS


@pytest.mark.integration
async def test_a_settle_asked_again_within_its_slack_writes_nothing(temp_db: Database) -> None:
    """A batch of files each asking for the same pass moves the waiting row once in a while, not
    once per file: every move is a turn of the single writer, and an import makes thousands."""
    await temp_db.initialize_schema()
    clock = FakeClock(1000)
    queue = JobQueue(temp_db, clock=clock.now)
    sweep = noop_handler()

    first = await queue.enqueue_when_settled(sweep)
    clock.advance(SETTLE_SLACK_SECONDS)
    assert await queue.enqueue_when_settled(sweep) == first
    job = await queue.get(first)
    assert job is not None and job.run_after == 1060 and job.updated_at == 1000, "left alone"

    clock.advance(1)
    await queue.enqueue_when_settled(sweep)
    job = await queue.get(first)
    assert job is not None and job.run_after == 1061 + SETTLE_SLACK_SECONDS, "moved once due"


@pytest.mark.integration
async def test_a_settle_onto_a_pass_already_due_writes_nothing(temp_db: Database) -> None:
    """A pass already due runs after this file's change whenever it is taken: nothing to move."""
    await temp_db.initialize_schema()
    clock = FakeClock(1000)
    queue = JobQueue(temp_db, clock=clock.now)
    sweep = noop_handler()
    due = await queue.enqueue(sweep)

    clock.advance(30)
    assert await queue.enqueue_when_settled(sweep) == due
    job = await queue.get(due)
    assert job is not None and job.run_after is None and job.updated_at == 1000


@pytest.mark.integration
async def test_a_files_work_is_handed_out_in_one_write(
    job_queue: JobQueue, temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each write is a turn of the single writer that every other write waits behind, so the
    work one file starts goes in together, in the order given, under the job that started it."""
    parent = await job_queue.enqueue(noop_handler("read"))
    kinds = [noop_handler("picture"), noop_handler("faces"), noop_handler("meaning")]
    blocks = 0
    write = temp_db.write

    @asynccontextmanager
    async def counted() -> AsyncIterator[object]:
        nonlocal blocks
        blocks += 1
        async with write() as connection:
            yield connection

    monkeypatch.setattr(temp_db, "write", counted)
    ids = await job_queue.enqueue_children(parent, [(kind, {"n": 1}) for kind in kinds])

    assert blocks == 1
    children = await job_queue.children(parent)
    assert [child.id for child in children] == ids
    assert [child.type for child in children] == kinds


@pytest.mark.integration
async def test_a_switched_off_child_queues_none_of_the_files_work(job_queue: JobQueue) -> None:
    """Refused before anything is written, as a single enqueue is."""

    async def off() -> bool:
        return False

    parent = await job_queue.enqueue(noop_handler("read"))
    job_queue.switchboard.declare(Switch(key="t.off", refusal="off", on=off), noop_handler("dup"))
    with pytest.raises(JobSwitchedOff):
        await job_queue.enqueue_children(parent, [(noop_handler("picture"), {}), ("dup", {})])
    assert await job_queue.children(parent) == []


@pytest.mark.integration
async def test_only_work_a_worker_could_take_now_wakes_the_idle_workers(
    job_queue: JobQueue,
) -> None:
    """An idle worker woken for a row it cannot take claims nothing inside the writer's lock:
    a row put off, a settle and a family's held work wait for their moment or their lift."""
    woken: list[int] = []
    job_queue.listen_for_work(lambda: woken.append(1))
    kind = noop_handler()

    await job_queue.enqueue(kind, run_after=int(time.time()) + 600)
    await job_queue.enqueue_when_settled(noop_handler("sweep"))
    assert woken == []

    scan = await job_queue.enqueue(noop_handler("scan"))
    assert len(woken) == 1
    await job_queue.hold_family(scan, spared=("read",))
    await job_queue.enqueue_children(scan, [(noop_handler("picture"), {})])
    assert len(woken) == 1, "held by its family"
    await job_queue.enqueue_children(scan, [(noop_handler("read"), {})])
    assert len(woken) == 2, "spared by the hold"


@pytest.mark.integration
async def test_a_press_collapsing_onto_settling_work_runs_it_now(job_queue: JobQueue) -> None:
    """A Run now that lands on a sweep waiting for an import to settle is a press, and a press runs
    now: the row's timing says so AND its wait is gone. Without the second half the row reads `now`
    and stays unclaimable for the hour the settle asked for, which nothing on any screen explains."""
    sweep = noop_handler()

    waiting = await job_queue.enqueue_when_settled(sweep)
    assert await job_queue.claim("worker-1") is None, "not claimable before it settles"

    pressed = await job_queue.enqueue(sweep, dedupe=True, at="now", requested_by="admin-1")

    assert pressed == waiting, "the press collapsed onto the sweep already waiting"
    job = await job_queue.get(waiting)
    assert job is not None and job.timing == "now" and job.run_after is None
    claimed = await job_queue.claim("worker-1")
    assert claimed is not None and claimed.id == waiting


@pytest.mark.integration
async def test_a_press_collapsing_onto_a_press_put_off_runs_it_now(job_queue: JobQueue) -> None:
    """The row's timing may already read `now` (an earlier press) and still carry a wait (put off
    to a later moment); a second press collapsing onto it clears the wait. Judged by the timing
    alone, the second press would land on a row that reads `now` and does not run."""
    sweep = noop_handler()

    later = int(time.time()) + 3600
    put_off = await job_queue.enqueue(sweep, dedupe=True, requested_by="admin-1", run_after=later)
    assert await job_queue.claim("worker-1") is None, "not claimable while put off"

    pressed = await job_queue.enqueue(sweep, dedupe=True, at="now", requested_by="admin-1")

    assert pressed == put_off, "the press collapsed onto the row already waiting"
    job = await job_queue.get(put_off)
    assert job is not None and job.timing == "now" and job.run_after is None
    claimed = await job_queue.claim("worker-1")
    assert claimed is not None and claimed.id == put_off


@pytest.mark.integration
async def test_a_job_is_claimable_straight_away_by_default(job_queue: JobQueue) -> None:
    """No `run_after` means no wait, which is what almost every job wants.

    Asserted rather than assumed, because the condition that implements the delay has to read a
    NULL as "no delay", and a version of it that read NULL as "never" would leave every ordinary
    job in the queue forever while every test that passes a time explicitly still went green.
    """
    job_id = await job_queue.enqueue(noop_handler(), {"asset_id": "01HQ"})

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.run_after is None

    claimed = await job_queue.claim(WORKER)
    assert claimed is not None
    assert claimed.id == job_id


@pytest.mark.integration
async def test_a_job_asked_to_wait_is_not_claimed_until_its_time(temp_db: Database) -> None:
    """The delay is enforced at the claim, and the same worker takes the job once the clock moves.

    One queue and one clock throughout: the job is invisible to a worker at 1000 and taken by that
    same worker at 2000, so what changed is the time and nothing else.
    """
    await temp_db.initialize_schema()
    clock = FakeClock(1000)
    queue = JobQueue(temp_db, clock=clock.now)

    job_id = await queue.enqueue(noop_handler(), {"asset_id": "01HQ"}, run_after=1500)

    assert await queue.claim(WORKER) is None

    clock.advance(1000)

    claimed = await queue.claim(WORKER)
    assert claimed is not None
    assert claimed.id == job_id


@pytest.mark.integration
async def test_a_waiting_job_does_not_hold_up_the_one_behind_it(temp_db: Database) -> None:
    """A job waiting for tomorrow is skipped over, not queued in front of everything else.

    This is the failure worth guarding: a delay implemented by ordering rather than by filtering
    would leave the scheduled job at the head of the queue and stall every job behind it, which
    looks exactly like a hung worker and would be debugged as one.
    """
    await temp_db.initialize_schema()
    clock = FakeClock(1000)
    queue = JobQueue(temp_db, clock=clock.now)

    job_type = noop_handler()
    # Enqueued first and with the better priority, so nothing but the wait can be keeping it back.
    await queue.enqueue(job_type, {"asset_id": "01HQ"}, priority=1, run_after=9999)
    ready = await queue.enqueue(job_type, {"asset_id": "01HR"}, priority=50)

    claimed = await queue.claim(WORKER)

    assert claimed is not None
    assert claimed.id == ready


@pytest.mark.integration
async def test_a_wait_that_has_already_passed_is_claimable_now(temp_db: Database) -> None:
    """The catch-up case, and the whole reason the wait is a column rather than a timer.

    A machine switched off for a week comes back to a row whose time went by while it was down. A
    timer would simply have missed it; a row with a past time is claimable immediately.
    """
    await temp_db.initialize_schema()
    clock = FakeClock(1_000_000)
    queue = JobQueue(temp_db, clock=clock.now)

    job_id = await queue.enqueue(noop_handler(), {"asset_id": "01HQ"}, run_after=1)

    claimed = await queue.claim(WORKER)

    assert claimed is not None
    assert claimed.id == job_id


@pytest.mark.integration
async def test_a_waiting_job_is_skipped_while_another_type_is_at_capacity(
    temp_db: Database,
) -> None:
    """The delay holds on the other claim statement too.

    There are two of them: one that excludes the job types already running as many as they are
    allowed, and one that does not, and a condition added to only the first would let a scheduled
    job run early on any machine that happened to have a capped type busy.
    """
    await temp_db.initialize_schema()
    clock = FakeClock(1000)
    queue = JobQueue(temp_db, clock=clock.now)

    await queue.enqueue(noop_handler("transcode"), {"asset_id": "01HQ"})
    running = await queue.claim(WORKER)
    assert running is not None

    await queue.enqueue(noop_handler(), {"asset_id": "01HR"}, run_after=9999)

    assert await queue.claim("worker-two", limits={"transcode": 1}) is None


@pytest.mark.integration
async def test_an_unknown_job_id_is_none(job_queue: JobQueue) -> None:
    assert await job_queue.get("01HQNOSUCHJOB0000000000000") is None


@pytest.mark.integration
async def test_a_job_type_with_no_handler_is_refused_at_enqueue(job_queue: JobQueue) -> None:
    """Fail where the mistake is. The alternative is three attempts and a log nobody reads."""
    with pytest.raises(UnknownJobType, match="thumbnail"):
        await job_queue.enqueue("thumbnail", {"asset_id": "A"})


@pytest.mark.integration
async def test_a_job_type_with_no_handler_can_be_queued_deliberately(job_queue: JobQueue) -> None:
    job_id = await job_queue.enqueue("thumbnail", require_handler=False)

    assert await job_queue.get(job_id) is not None


@pytest.mark.unit
def test_a_handler_cannot_be_registered_twice() -> None:
    noop_handler("scan")

    with pytest.raises(ValueError, match="already registered"):
        noop_handler("scan")


@pytest.mark.unit
def test_the_handler_registry_is_handed_out_as_a_copy() -> None:
    noop_handler("scan")

    registered_handlers().clear()

    assert "scan" in registered_handlers()


@pytest.mark.integration
async def test_a_job_must_be_allowed_at_least_one_attempt(job_queue: JobQueue) -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        await job_queue.enqueue(noop_handler(), max_attempts=0)


@pytest.mark.integration
async def test_max_attempts_has_a_ceiling(job_queue: JobQueue) -> None:
    """The floor is not the only bound. A runaway retry count (a user-influenced value a careless
    slice forwarded) is refused too, so a job cannot be made to retry far past the point where
    retrying is only failing more, more expensively. The ceiling itself is allowed."""
    job_type = noop_handler()
    assert await job_queue.enqueue(job_type, max_attempts=MAX_ATTEMPTS_CEILING)
    with pytest.raises(ValueError, match="max_attempts"):
        await job_queue.enqueue(job_type, max_attempts=MAX_ATTEMPTS_CEILING + 1)


@pytest.mark.integration
async def test_priority_is_bounded_to_a_band(job_queue: JobQueue) -> None:
    """Priority is server-set and lower-runs-first. A value outside the band (one that would jump
    the whole queue, or sink below everything) is refused rather than stored. The edges are in."""
    job_type = noop_handler()
    assert await job_queue.enqueue(job_type, priority=PRIORITY_MIN)
    assert await job_queue.enqueue(job_type, priority=PRIORITY_MAX)
    with pytest.raises(ValueError, match="priority"):
        await job_queue.enqueue(job_type, priority=PRIORITY_MIN - 1)
    with pytest.raises(ValueError, match="priority"):
        await job_queue.enqueue(job_type, priority=PRIORITY_MAX + 1)


# --- payloads carry ids, not paths ------------------------------------------------------


@pytest.mark.unit
@pytest.mark.regression
@pytest.mark.parametrize(
    "payload",
    [
        {"path": "/home/kate/Videos/holiday.mp4"},
        {"file": "C:\\Users\\Kate\\clip.mp4"},
        {"share": "\\\\nas\\media\\clip.mp4"},
        {"paths": ["/srv/media/a.mp4"]},
        {"nested": {"deeper": {"where": "/etc/passwd"}}},
        {"items": [{"path": "/var/lib/x"}]},
    ],
)
def test_a_payload_may_not_carry_a_file_path(payload: dict[str, object]) -> None:
    """A path in a payload is a path in every log line, backup and export the job appears in.

    It is also an instruction: a job that takes a path can be pointed at any file on the machine
    by whoever can enqueue one. Ids resolve to a row the handler is allowed to touch; paths do not.
    """
    with pytest.raises(ValueError, match="path"):
        _check_payload(payload)


@pytest.mark.unit
@pytest.mark.parametrize(
    "payload",
    [
        {"asset_id": "01HQ7Z9KQ0000000000000000A"},
        {"url": "https://example.com/watch?v=1"},
        {"name": "holiday.mp4"},
        {"root_id": "01HQ", "depth": 3, "recursive": True, "after": None},
        {"asset_ids": ["01HQ", "01HR"]},
    ],
)
def test_a_payload_of_ids_and_urls_is_fine(payload: dict[str, object]) -> None:
    _check_payload(payload)


@pytest.mark.unit
@pytest.mark.regression
@pytest.mark.parametrize(
    "payload",
    [
        {"path": "../../../etc/passwd"},
        {"file": "foo/../../secret"},
        {"key": "~/.ssh/id_rsa"},
        {"win": "..\\..\\Windows\\System32\\config"},
        {"nested": {"deeper": {"where": "a/b/../../../etc/shadow"}}},
        {"items": ["ok", "../escape"]},
        {"bare": ".."},
    ],
)
def test_a_payload_may_not_carry_a_relative_traversal(payload: dict[str, object]) -> None:
    """The absolute-path check is only half the rule. A relative `..` walks out of wherever a
    careless handler joined it just as surely, and a `~` is a path the shell expands to one, so
    both are refused where the payload is written, not left for a handler to remember."""
    with pytest.raises(ValueError, match="path"):
        _check_payload(payload)


@pytest.mark.integration
@pytest.mark.regression
async def test_enqueue_refuses_a_payload_with_a_path(job_queue: JobQueue) -> None:
    with pytest.raises(ValueError, match="path"):
        await job_queue.enqueue(noop_handler(), {"path": "/home/kate/clip.mp4"})

    assert await job_queue.counts() == {}


# --- what a stored error is allowed to say ----------------------------------------------


@pytest.mark.unit
def test_a_stored_job_error_loses_the_name_even_when_logs_are_unredacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SIFT_LOG_UNREDACTED reveals names in an admin's own live log stream: their choice about
    their own screen. A job error is different: it is written to the database and rides out of the
    machine in a backup or a diagnostics export, where that choice does not travel with it. So a
    stored error loses the name whatever the log toggle says, while the useful shape survives.
    """
    from sift.kernel import log as log_module

    monkeypatch.setattr(log_module, "_redact_personal", False)  # logs set to reveal names

    stored = _for_the_record("failed reading /home/realname/Videos/private.mp4")

    assert "realname" not in stored  # the name is gone from the persisted value
    assert "private.mp4" in stored  # the shape that makes it diagnosable survives


# --- the claim --------------------------------------------------------------------------


@pytest.mark.integration
async def test_claiming_takes_the_job_and_counts_the_attempt(job_queue: JobQueue) -> None:
    job_id = await job_queue.enqueue(noop_handler())

    claimed = await job_queue.claim(WORKER)

    assert claimed is not None
    assert claimed.id == job_id
    assert claimed.state is JobState.RUNNING
    assert claimed.claimed_by == WORKER
    assert claimed.attempts == 1
    assert claimed.heartbeat_at is not None


@pytest.mark.integration
async def test_claiming_an_empty_queue_returns_nothing(job_queue: JobQueue) -> None:
    assert await job_queue.claim(WORKER) is None


@pytest.mark.integration
async def test_a_claimed_job_cannot_be_claimed_again(job_queue: JobQueue) -> None:
    await job_queue.enqueue(noop_handler())

    assert await job_queue.claim(WORKER) is not None
    assert await job_queue.claim(OTHER_WORKER) is None


@pytest.mark.integration
async def test_the_claim_takes_one_job_and_leaves_the_rest(job_queue: JobQueue) -> None:
    noop_handler("probe")
    for _ in range(3):
        await job_queue.enqueue("probe")

    await job_queue.claim(WORKER)

    assert await job_queue.counts() == {"running": 1, "queued": 2}


@pytest.mark.integration
async def test_jobs_run_by_priority_then_by_age(temp_db: Database) -> None:
    clock = FakeClock()
    queue = JobQueue(temp_db, clock=clock.now)
    await temp_db.initialize_schema()
    noop_handler()

    first_but_low = await queue.enqueue("probe", {"n": 1}, priority=200)
    clock.advance(1)
    urgent_and_older = await queue.enqueue("probe", {"n": 2}, priority=10)
    clock.advance(1)
    urgent_and_newer = await queue.enqueue("probe", {"n": 3}, priority=10)

    order = []
    while (job := await queue.claim(WORKER)) is not None:
        order.append(job.id)
        await queue.complete(job.id, WORKER)

    assert order == [urgent_and_older, urgent_and_newer, first_but_low]


@given(
    jobs=st.lists(
        st.tuples(st.integers(min_value=0, max_value=9), st.integers(min_value=0, max_value=3)),
        min_size=1,
        max_size=10,
    )
)
@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@pytest.mark.unit
def test_claims_always_come_out_in_priority_then_age_order(
    tmp_path_factory: pytest.TempPathFactory, jobs: list[tuple[int, int]]
) -> None:
    """The ordering is what makes priority mean anything, so it is checked against every shape.

    Driven synchronously with its own database per example: a function-scoped fixture is created
    once and reused across every example, which would let one example's rows leak into the next.
    """

    async def run() -> list[tuple[int, int]]:
        path = tmp_path_factory.mktemp("order") / "jobs.sqlite3"
        database = Database(path)
        await database.connect()
        try:
            await database.initialize_schema()
            clock = FakeClock()
            queue = JobQueue(database, clock=clock.now)

            for index, (priority, gap) in enumerate(jobs):
                clock.advance(gap)
                await queue.enqueue("probe", {"i": index}, priority=priority, require_handler=False)

            claimed = []
            while (job := await queue.claim(WORKER)) is not None:
                claimed.append((job.priority, job.created_at))
                await queue.complete(job.id, WORKER)
            return claimed
        finally:
            await database.close()

    claimed = asyncio.run(run())

    assert len(claimed) == len(jobs)
    assert claimed == sorted(claimed)


# --- per-type limits --------------------------------------------------------------------


@pytest.mark.integration
async def test_a_capped_type_stops_being_claimed_once_it_is_at_its_limit(
    job_queue: JobQueue,
) -> None:
    """One transcode at a time, however many workers are free."""
    noop_handler("transcode")
    for _ in range(3):
        await job_queue.enqueue("transcode")

    assert await job_queue.claim(WORKER, limits={"transcode": 1}) is not None
    assert await job_queue.claim(OTHER_WORKER, limits={"transcode": 1}) is None


@pytest.mark.integration
async def test_a_capped_type_does_not_block_anything_else(job_queue: JobQueue) -> None:
    """The failure this prevents: a burst of transcodes fills the pool and nothing else runs."""
    noop_handler("transcode")
    noop_handler("thumbnail")
    await job_queue.enqueue("transcode")
    await job_queue.enqueue("transcode")
    thumbnail = await job_queue.enqueue("thumbnail")

    limits = {"transcode": 1}
    await job_queue.claim(WORKER, limits=limits)
    second = await job_queue.claim(OTHER_WORKER, limits=limits)

    assert second is not None
    assert second.id == thumbnail


@pytest.mark.integration
async def test_a_capped_type_is_claimable_again_once_one_finishes(job_queue: JobQueue) -> None:
    noop_handler("transcode")
    await job_queue.enqueue("transcode")
    await job_queue.enqueue("transcode")

    limits = {"transcode": 1}
    first = await job_queue.claim(WORKER, limits=limits)
    assert first is not None
    await job_queue.complete(first.id, WORKER)

    assert await job_queue.claim(OTHER_WORKER, limits=limits) is not None


@pytest.mark.integration
async def test_an_uncapped_type_runs_as_many_as_there_are_workers(job_queue: JobQueue) -> None:
    noop_handler("thumbnail")
    for _ in range(3):
        await job_queue.enqueue("thumbnail")

    limits = {"transcode": 1}
    assert await job_queue.claim(WORKER, limits=limits) is not None
    assert await job_queue.claim(OTHER_WORKER, limits=limits) is not None


@pytest.mark.integration
async def test_a_limit_of_zero_pauses_a_type_rather_than_being_refused(job_queue: JobQueue) -> None:
    """Zero is a cap, not an error.

    Recognition's "only overnight" setting resolves to zero outside its hours, deliberately, because
    stopping is what that setting means. Refused, it would become a `ValueError` inside the pool's
    own reconfigure, where it is caught, logged and dropped, so **every** live setting would stop
    taking effect along with it, including the worker count.
    """
    pool = WorkerPool(job_queue, concurrency=1, limits={"transcode": 0})

    assert pool.limits == {"transcode": 0}


@pytest.mark.integration
async def test_a_negative_limit_is_still_refused(job_queue: JobQueue) -> None:
    """A negative cap is not a smaller one, and nothing anywhere means to ask for it."""
    with pytest.raises(ValueError, match="negative"):
        WorkerPool(job_queue, concurrency=1, limits={"transcode": -1})


@pytest.mark.integration
async def test_a_paused_type_does_not_run_even_with_nothing_of_it_running(
    job_queue: JobQueue,
) -> None:
    """The half that has to be enforced rather than merely accepted.

    A paused type has nothing running, so it never appears in a count of what IS running, and a
    capacity check that reads only those rows would let the one thing that must not start be the
    one thing that does.
    """
    paused = noop_handler("paused_type")
    pool = WorkerPool(job_queue, concurrency=2, poll_interval=0.01, limits={paused: 0})
    await job_queue.enqueue(paused, {})
    await pool.start()
    try:
        await asyncio.sleep(0.1)
        assert await job_queue.outstanding(paused) == 1  # still waiting, never claimed
    finally:
        await pool.stop()


@pytest.mark.integration
async def test_a_pool_needs_a_worker(job_queue: JobQueue) -> None:
    with pytest.raises(ValueError, match="at least one worker"):
        WorkerPool(job_queue, concurrency=0)


async def _a_read_and_its_work(job_queue: JobQueue) -> tuple[str, str]:
    for kind in ("walk", "per_file", "elsewhere"):
        noop_handler(kind)
    walk = await job_queue.enqueue("walk")
    assert await job_queue.claim(WORKER) is not None
    per_file = await job_queue.enqueue("per_file", parent_id=walk)
    return walk, per_file


@pytest.mark.integration
@pytest.mark.parametrize("limits", [None, {"capped": 0}])
async def test_a_held_family_waits_while_other_work_runs(
    job_queue: JobQueue, limits: dict[str, int] | None
) -> None:
    walk, per_file = await _a_read_and_its_work(job_queue)
    elsewhere = await job_queue.enqueue("elsewhere")
    await job_queue.hold_family(walk, spared=["walk"])

    first = await job_queue.claim(OTHER_WORKER, limits=limits)
    assert first is not None and first.id == elsewhere
    assert await job_queue.claim(OTHER_WORKER, limits=limits) is None

    assert job_queue.lift_hold(walk) and not job_queue.lift_hold(walk)
    after = await job_queue.claim(OTHER_WORKER, limits=limits)
    assert after is not None and after.id == per_file


@pytest.mark.integration
@pytest.mark.parametrize("limits", [None, {"capped": 0}])
async def test_a_row_pressed_now_and_the_holders_own_kinds_pass_a_hold(
    job_queue: JobQueue, limits: dict[str, int] | None
) -> None:
    walk, _ = await _a_read_and_its_work(job_queue)
    pressed = await job_queue.enqueue("per_file", {"n": 2}, parent_id=walk, at=AT_NOW)
    sibling = await job_queue.enqueue("walk", {"n": 3}, parent_id=walk)
    await job_queue.hold_family(walk, spared=["walk"])

    taken = [await job_queue.claim(OTHER_WORKER, limits=limits) for _ in range(3)]

    assert [None if one is None else one.id for one in taken] == [pressed, sibling, None]


@pytest.mark.integration
async def test_held_work_is_not_demand_and_says_which_job_holds_it(job_queue: JobQueue) -> None:
    walk, _ = await _a_read_and_its_work(job_queue)
    await job_queue.enqueue("elsewhere")
    assert await job_queue.holder_of(["per_file"]) is None
    assert await job_queue.held_for_family_by_type() == {}

    await job_queue.hold_family(walk, spared=["walk"])

    assert await job_queue.demand_by_type() == {"walk": 1, "elsewhere": 1}
    assert await job_queue.held_for_family_by_type() == {"per_file": 1}
    assert await job_queue.holder_of(["per_file", "thumbnail"]) == walk
    assert await job_queue.holder_of(["elsewhere"]) is None


@pytest.mark.integration
async def test_a_hold_ends_with_its_job_however_the_handler_ends(job_queue: JobQueue) -> None:
    walk, per_file = await _a_read_and_its_work(job_queue)
    job = await job_queue.get(walk)
    assert job is not None
    context = JobContext(job=job, worker_id=WORKER, queue=job_queue)

    async def handler(context: JobContext) -> None:
        await context.hold_own_family(spared=["walk"])
        raise RuntimeError("the read failed")

    with pytest.raises(RuntimeError):
        await WorkerPool(job_queue, concurrency=1)._invoke(handler, context)

    assert not context.lift_own_hold()
    after = await job_queue.claim(OTHER_WORKER)
    assert after is not None and after.id == per_file


@pytest.mark.integration
async def test_a_press_over_several_files_is_one_family(job_queue: JobQueue) -> None:
    """A press heads ONE row on the queue, every new file a step under it. A row it collapsed
    onto keeps its own family, and work nobody pressed stays a row a file."""
    kind = noop_handler()
    waiting = await job_queue.enqueue(kind, {"asset_id": "A0"}, dedupe=True)
    placed = await job_queue.enqueue_many(
        kind,
        [{"asset_id": "A0"}, {"asset_id": "A1"}, {"asset_id": "A2"}, {"asset_id": "A3"}],
        dedupe=True,
        requested_by="acct-1",
    )

    assert placed[0] == waiting
    steps = [await job_queue.get(one) for one in placed[1:]]
    tops = await job_queue.list(tops_only=True)
    (head,) = [job for job in tops.jobs if job.id != waiting]
    assert [one.parent_id for one in steps if one is not None] == [head.id] * 3
    assert (await job_queue.step_counts([head.id]))[head.id].by_state == {"queued": 3}

    plain = await job_queue.enqueue_many(kind, [{"asset_id": "B1"}, {"asset_id": "B2"}])
    for one in plain:
        job = await job_queue.get(one)
        assert job is not None and job.parent_id is None


@pytest.mark.integration
async def test_a_press_is_headed_by_a_row_in_its_own_words_over_every_file(
    job_queue: JobQueue,
) -> None:
    kind = noop_handler()
    placed = await job_queue.enqueue_many(
        kind,
        [{"asset_id": f"A{n}"} for n in range(4)],
        requested_by="acct-1",
        title="Creating hover previews for 4 files",
    )
    (top,) = (await job_queue.list(tops_only=True)).jobs
    assert top.payload == {"asset_id": "A0", "title": "Creating hover previews for 4 files"}
    assert top.state is JobState.DONE and top.type == "press" and top.id not in placed
    assert (await job_queue.step_counts([top.id]))[top.id].by_state == {"queued": 4}
    collapsed = await job_queue.enqueue_many(
        kind, [{"asset_id": "A0"}], requested_by="acct-1", dedupe=True, title="Again"
    )
    assert collapsed == [placed[0]]
    assert len((await job_queue.list(tops_only=True)).jobs) == 1, "a head that heads nothing"


@pytest.mark.integration
async def test_a_settle_that_has_come_due_goes_before_the_work_of_its_urgency(
    temp_db: Database,
) -> None:
    """A settle asked for during a run waits its minute, then goes ahead of the run's rows at its
    urgency, so its answer appears during the run; work more urgent still goes first."""
    await temp_db.initialize_schema()
    clock = FakeClock(1000)
    queue = JobQueue(temp_db, clock=clock.now)
    kind = noop_handler()
    backlog = [await queue.enqueue(kind, {"asset_id": f"A{n}"}) for n in range(3)]
    sweep = await queue.enqueue(noop_handler("sweep"), run_after=1060)
    pressed = await queue.enqueue(noop_handler("pressed"), priority=50)
    clock.advance(60)

    claimed = []
    while (job := await queue.claim(WORKER)) is not None:
        claimed.append(job.id)
    assert claimed == [pressed, sweep, *backlog]


@pytest.mark.integration
async def test_a_claim_passes_a_kind_at_its_cap_without_reading_its_rows(
    job_queue: JobQueue,
) -> None:
    """The claim seeks each kind's head, so work at its cap costs nothing however much of it waits,
    rather than a walk of every waiting row ahead of the first it may take."""
    from sift.kernel.jobs.queue_claim import _CLAIM, claim_parameters

    capped, other = noop_handler("capped"), noop_handler("other")
    for n in range(30):
        await job_queue.enqueue(capped, {"asset_id": f"A{n}"})
    last = await job_queue.enqueue(other)

    job = await job_queue.claim(WORKER, limits={capped: 0})

    assert job is not None and job.id == last
    asked = claim_parameters(WORKER, 0, [capped], (True, "[]"), (True, "[]", "[]"), None)
    explained = "EXPLAIN QUERY PLAN " + _CLAIM  # nosemgrep: sift-no-string-built-sql
    plan = await job_queue._db.fetch_all(explained, asked)  # nosemgrep: sift-no-string-built-sql
    details = " ".join(str(row["detail"]) for row in plan)
    assert "USING INDEX ix_jobs_claim_heads" in details
    assert "ix_jobs_claim_by_id" not in details


@pytest.mark.integration
async def test_work_of_a_kind_at_its_cap_wakes_nobody(job_queue: JobQueue) -> None:
    """The worker that ends the running one claims next, so an idle worker woken for it would claim
    nothing inside the writer's lock."""
    woken: list[int] = []
    job_queue.listen_for_work(lambda: woken.append(1))
    capped = noop_handler("capped")
    await job_queue.enqueue(capped)
    assert len(woken) == 1
    assert await job_queue.claim(WORKER, limits={capped: 1}) is not None
    assert await job_queue.claim(OTHER_WORKER, limits={capped: 1}) is None
    await job_queue.enqueue(capped)
    assert len(woken) == 1, "at its cap as the last claim found it"
    await job_queue.enqueue(noop_handler("free"))
    assert len(woken) == 2


@pytest.mark.integration
async def test_a_retry_wakes_every_idle_worker(job_queue: JobQueue) -> None:
    """Rows a retry or a resume puts back name no arrival: every idle worker is told."""
    told: list[int] = []
    job_queue.listen_for_anything(lambda: told.append(1))
    job_id = await job_queue.enqueue(noop_handler())
    await job_queue.cancel(job_id)
    assert await job_queue.retry(job_id)
    assert told == [1]


@pytest.mark.integration
async def test_a_settle_asked_from_a_job_is_as_urgent_as_that_job(job_queue: JobQueue) -> None:
    """A pressed run's files ask for their whole-library passes at the run's urgency, so the pass
    runs during the run rather than after the whole of it; outside a job the asked one holds."""
    from sift.kernel.jobs.queue_core import ASKED_AT

    sweep, other = noop_handler("sweep"), noop_handler("other")
    token = ASKED_AT.set(50)
    try:
        asked = await job_queue.enqueue_when_settled(sweep, priority=200)
    finally:
        ASKED_AT.reset(token)
    alone = await job_queue.enqueue_when_settled(other, priority=200)
    first, second = await job_queue.get(asked), await job_queue.get(alone)
    assert first is not None and first.priority == 50
    assert second is not None and second.priority == 200


@pytest.mark.integration
async def test_the_live_lookups_read_the_live_rows_index(job_queue: JobQueue) -> None:
    """ "Is this already asked for" is a seek of the live rows by type and payload, never a walk of
    a type's rows; one read answers for a job asked for in two shapes."""
    from sift.kernel.jobs.queue_enqueue import _PENDING_LIKE
    from sift.kernel.jobs.queue_reads import _LIVE_LIKE

    kind = noop_handler()
    await job_queue.enqueue(kind, {"asset_id": "A", "shape": 2})
    assert await job_queue.any_live(kind, [{"asset_id": "A"}, {"asset_id": "A", "shape": 2}])
    assert not await job_queue.any_live(kind, [{"asset_id": "A"}, {"asset_id": "B"}])
    for sql, params in ((_PENDING_LIKE, (kind, "{}")), (_LIVE_LIKE, (kind, '["{}"]'))):
        explained = "EXPLAIN QUERY PLAN " + sql  # nosemgrep: sift-no-string-built-sql
        db = job_queue._db
        plan = await db.fetch_all(explained, params)  # nosemgrep: sift-no-string-built-sql
        assert any("ix_jobs_live_payload" in str(row["detail"]) for row in plan), plan


@pytest.mark.integration
async def test_a_walk_asked_for_runs_beside_a_whole_walk_at_its_cap(job_queue: JobQueue) -> None:
    """A walk at its cap of one is the whole library's for hours; a folder somebody pressed or a few
    files the watcher named take one place of their own beside it, and only one."""
    from sift.kernel.jobs.families import Family

    async def walk(_context: JobContext) -> None:
        return None

    register_handler("walk", walk, name="Test walk", family=Family.SCAN)
    whole = await job_queue.enqueue("walk", {"root_id": "r"})
    named = await job_queue.enqueue("walk", {"root_id": "r", "paths": ["a.png"]})
    folder = await job_queue.enqueue("walk", {"root_id": "r", "folder_id": "f"})
    first = await job_queue.claim(WORKER, limits={"walk": 1})
    assert first is not None and first.id == whole
    assert await job_queue.claim("paused", limits={"walk": 0}) is None, "paused is paused"

    beside = await job_queue.claim(OTHER_WORKER, limits={"walk": 1})
    assert beside is not None and beside.id == named
    assert await job_queue.claim("a third", limits={"walk": 1}) is None, "one place, not two"
    await job_queue.complete(named, OTHER_WORKER)
    after = await job_queue.claim(OTHER_WORKER, limits={"walk": 1})
    assert after is not None and after.id == folder


@pytest.mark.integration
async def test_a_files_first_read_goes_before_older_work_of_its_urgency(
    job_queue: JobQueue,
) -> None:
    """With no hold, the files a walk has yet to read would wait behind every older file's later
    steps; the read goes first up to its cap, and the oldest work takes every other worker."""
    from sift.kernel.jobs.families import Family

    async def read(_context: JobContext) -> None:
        return None

    register_handler("read", read, name="Test read", family=Family.SCAN)
    later = noop_handler("later")
    older = [await job_queue.enqueue(later, {"asset_id": f"A{n}"}) for n in range(2)]
    first = await job_queue.enqueue("read", {"asset_id": "B"})

    claimed = [await job_queue.claim(WORKER, limits={"read": 1}) for _ in range(3)]
    assert [job.id for job in claimed if job is not None] == [first, *older]
