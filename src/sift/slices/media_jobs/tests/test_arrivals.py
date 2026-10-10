# SPDX-License-Identifier: AGPL-3.0-or-later
"""A 136-second import of 36 files into a library whose Identify had 2,691 waiting for their task:
while only the arriving files run, the time left is theirs."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import pytest

from sift.kernel.db import Database
from sift.kernel.jobs import register_handler
from sift.kernel.jobs.families import Family
from sift.kernel.jobs.holding import Holding
from sift.kernel.jobs.ledger import Ledger
from sift.kernel.jobs.queue_rows import LiveWork
from sift.kernel.jobs.switchboard import Switchboard
from sift.kernel.jobs.time_left import MEASURING
from sift.slices.media_jobs.activity_families import _families
from sift.slices.media_jobs.presses import Presses
from sift.slices.media_jobs.router import FamilyOfWork, KindOfWork

pytestmark = pytest.mark.integration

#: Seconds in, Identify's waiting and outstanding, and its run's files finished by then.
POLLS = ((17, 2729, 30, 0), (33, 2727, 40, 2), (71, 2727, 24, 33))
FINISHED = 136
STANDING = 2691


@dataclass
class _Pool:
    concurrency: int = 1
    limits: dict[str, int] = field(default_factory=dict)
    holding: Holding = field(default_factory=Holding)


async def _nothing(_context: object) -> None:
    return None


async def _ledger(temp_db: Database) -> Ledger:
    await temp_db.initialize_schema()
    book = Ledger(temp_db, families_of={"facing": Family.IDENTIFY})
    await book.start()
    await temp_db.execute(
        "INSERT INTO work_runs (id, family, started_at, updated_at, finished_at, jobs_done,"
        " worker_ms, files) VALUES ('R1', 'identify', 1, 101, 101, 200, 40000, ?)",
        ('{"image": {"n": 200, "bytes": 0, "ms": 40000}}',),
    )
    return book


def _running_for(book: Ledger, seconds: float, files: int) -> None:
    book._open.clear()
    book.started("facing")
    run = book.open_run(Family.IDENTIFY)
    assert run is not None
    run.began = time.monotonic() - seconds
    for _ in range(files):
        book.finished("facing", duration_ms=12_000.0, ok=True, media_type="video")


async def _identify(book: Ledger, waiting: int, outstanding: int, **asked: object) -> FamilyOfWork:
    work = {
        "facing": KindOfWork(
            done=0, outstanding=outstanding, failed=0, waiting=waiting, total=waiting
        )
    }
    families = await _families(work, book, Switchboard(), held={}, **asked)  # type: ignore[arg-type]
    return families["identify"]


async def test_while_only_arriving_files_run_the_time_left_is_theirs(temp_db: Database) -> None:
    register_handler("facing", _nothing, name="Facing", family=Family.IDENTIFY, by_itself=True)
    book = await _ledger(temp_db)

    for seconds, waiting, outstanding, finished in POLLS:
        _running_for(book, seconds, finished)
        arriving = waiting - STANDING
        row = await _identify(
            book,
            waiting,
            outstanding,
            standing={"facing": STANDING},
            arriving={"facing": {"video": arriving}},
        )
        left = FINISHED - seconds
        assert row.quick_seconds is None or row.quick_seconds <= left <= (row.slow_seconds or 0)
        assert row.for_task == "2,691 more wait for their task."
        assert row.waiting == waiting

    # With a pool and no rate of its own measured yet, the row says so rather than guess.
    before = await _identify(book, 2727, 24, pool=_Pool())
    assert (before.quick_seconds, before.time_unknown) == (None, MEASURING)
    # Thirty files in five minutes of work due: the whole backlog at that rate is hours.
    clock = time.monotonic()
    for _tick in range(20):
        book.throughput(Family.IDENTIFY).busy(clock, 15.0)
    book.throughput(Family.IDENTIFY).done(clock, 30)
    priced = await _identify(book, 2727, 24, pool=_Pool())
    assert priced.quick_seconds is not None and priced.quick_seconds > 3600
    assert priced.for_task is None


async def test_with_no_arriving_file_left_to_price_the_time_says_what_waits(
    temp_db: Database,
) -> None:
    register_handler("facing", _nothing, name="Facing", family=Family.IDENTIFY, by_itself=True)
    book = await _ledger(temp_db)
    _running_for(book, 71, 33)

    row = await _identify(book, STANDING, 1, standing={"facing": STANDING}, arriving={})

    assert (row.quick_seconds, row.slow_seconds) == (None, None)
    assert row.time_unknown == "2,691 wait for their task."
    assert row.for_task is None


async def test_a_pass_nobody_runs_and_a_run_of_its_task_price_everything_waiting(
    temp_db: Database,
) -> None:
    register_handler("facing", _nothing, name="Facing", family=Family.IDENTIFY, by_itself=True)
    register_handler(
        "carrying", _nothing, name="Carrying", family=Family.IDENTIFY, carries_products=True
    )
    book = await _ledger(temp_db)
    split = {"standing": {"facing": STANDING}, "arriving": {"facing": {"video": 36}}}
    carried = [
        LiveWork(type="carrying", pressed=False, products=("faces",), again=False, count=2, since=0)
    ]

    idle = await _identify(book, 2727, 0, **split)
    pressed = await _identify(book, 2727, 24, presses={Family.IDENTIFY: Presses(live=3)}, **split)
    running = await _identify(book, 2727, 24, queue=object(), live=carried, **split)

    for row in (idle, pressed, running):
        assert row.for_task is None and row.time_unknown is None
    assert idle.quick_seconds == int(2727 * 0.2)
