# SPDX-License-Identifier: AGPL-3.0-or-later
"""The read and each pass after it at its own measured pace, a pass taking its share of the read's
workers once the read ends, "Measuring." before either, a floor while a walk counts or a kind is
unpriced, held steady, a stopped row said, each kept."""

from __future__ import annotations

import importlib
import time
from types import SimpleNamespace
from typing import Any

import pytest

# The `ran` event lands in the workbench's table.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.db import Database
from sift.kernel.jobs.families import Family
from sift.kernel.jobs.ledger import Ledger
from sift.kernel.jobs.time_left import MEASURING, Steady
from sift.slices.media_jobs import pooled
from sift.slices.media_jobs.activity_wire import FamilyOfWork, KindOfWork, PartOfWork
from sift.slices.media_jobs.pooled import STALLED, priced_together

FAMILIES = {"probe": Family.SCAN, "thumbnail": Family.GENERATE}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pooled, "_STEADY", Steady())


async def _book(temp_db: Database, **paces: float) -> Ledger:
    """Ten pictures read at 2 s and ten made at 1 s, then ten minutes of work due for each family
    named, at that many worker seconds a second."""
    await temp_db.initialize_schema()
    book = Ledger(temp_db, families_of=FAMILIES)
    for job_type, ms in (("probe", 2000), ("thumbnail", 1000)):
        book.started(job_type)
        for _ in range(10):
            book.finished(job_type, duration_ms=ms, ok=True, media_type="image")
    book._throughput.clear()
    clock = time.monotonic()
    for name, pace in paces.items():
        throughput = book.throughput(Family(name))
        for minute in range(10):
            at = clock - 60.0 * (9 - minute)
            for _tick in range(4):
                throughput.busy(at, 15.0)
            throughput.done(at, 10, 0, 60 * pace)
    return book


def _row(label: str, types: list[str], waiting: int, **more: Any) -> FamilyOfWork:
    return FamilyOfWork(label=label, types=types, waiting=waiting, task=None, **more)


def _answer(**generate: Any) -> dict[str, FamilyOfWork]:
    return {
        "scan": _row("Scan", ["probe", "scan"], 100, outstanding=4, running=4),
        "generate": _row(
            "Generate", ["thumbnail"], 100, **{"outstanding": 4, "running": 4, **generate}
        ),
    }


WORK = {
    "probe": KindOfWork(done=10, outstanding=4, failed=0, waiting=100),
    "thumbnail": KindOfWork(done=10, outstanding=4, failed=0, waiting=100),
    "scan": KindOfWork(done=0, outstanding=1, failed=0),
}
KINDS = {"probe": {"image": 100.0}, "thumbnail": {"image": 100.0}}
NO_READ = {**WORK, "probe": KindOfWork(done=10, outstanding=0, failed=0, waiting=0)}


async def _said(temp_db: Database) -> list[tuple[object, ...]]:
    rows = await temp_db.fetch_all(
        "SELECT quick, slow, left, stalled FROM said_times ORDER BY slow"
    )
    return [tuple(row) for row in rows]


async def test_the_read_and_each_pass_after_it_go_at_their_own_measured_pace(
    temp_db: Database,
) -> None:
    book = await _book(temp_db, scan=2.0, generate=0.5)
    answer = await priced_together(_answer(), WORK, KINDS, book, 4, False)

    # 200 worker seconds of reading at 2 a second: 100 s. 100 of pictures at a half: 50 done when
    # the read ends, the other 50 at the half and the read's 2: 120 s.
    assert (answer["scan"].quick_seconds, answer["scan"].slow_seconds) == (62, 160)
    assert (answer["generate"].quick_seconds, answer["generate"].slow_seconds) == (75, 192)
    assert await _said(temp_db) == [(62, 160, 100, 0), (75, 192, 100, 0)]


async def test_a_pass_with_no_pace_of_its_own_yet_works_with_the_reads_workers_after_it(
    temp_db: Database,
) -> None:
    book = await _book(temp_db, scan=2.0)
    answer = await priced_together(_answer(), WORK, KINDS, book, 4, False)
    # 100 s of reading, then 100 worker seconds at the read's 2: 150 s.
    assert (answer["generate"].quick_seconds, answer["generate"].slow_seconds) == (93, 240)


async def test_a_kind_nobody_priced_makes_the_pass_a_floor_from_the_rest(
    temp_db: Database,
) -> None:
    book = await _book(temp_db, scan=2.0, generate=0.5)
    work = {**WORK, "preview": KindOfWork(done=0, outstanding=1, failed=0, waiting=5)}
    answer = _answer()
    answer["generate"] = answer["generate"].model_copy(update={"types": ["thumbnail", "preview"]})
    priced = await priced_together(answer, work, KINDS, book, 4, False)
    generate = priced["generate"]
    assert (generate.quick_seconds, generate.slow_seconds, generate.at_least) == (75, 75, True)
    # A floor is no window to score the finish against.
    assert await _said(temp_db) == [(62, 160, 100, 0)]


async def test_while_a_walk_counts_every_time_is_the_least_the_counted_files_take(
    temp_db: Database,
) -> None:
    book = await _book(temp_db, scan=2.0, generate=0.5)
    answer = await priced_together(_answer(), WORK, KINDS, book, 4, False, counting=True)
    floors = {
        key: (row.quick_seconds, row.slow_seconds, row.at_least) for key, row in answer.items()
    }
    assert floors == {"scan": (62, 62, True), "generate": (75, 75, True)}
    assert await _said(temp_db) == []


async def test_before_the_read_has_a_pace_every_row_says_measuring(temp_db: Database) -> None:
    book = await _book(temp_db)
    answer = await priced_together(
        _answer(quick_seconds=5, slow_seconds=9), WORK, KINDS, book, 4, True
    )
    for key in ("scan", "generate"):
        row = answer[key]
        assert (row.quick_seconds, row.slow_seconds, row.time_unknown) == (None, None, MEASURING)
    assert await _said(temp_db) == []


async def test_a_pass_with_no_read_ahead_goes_at_its_own_measured_pace_less_its_tasks_files(
    temp_db: Database,
) -> None:
    book = await _book(temp_db, generate=0.5)
    answer = {"generate": _row("Generate", ["thumbnail"], 100, outstanding=4, running=4)}
    # 100 worker seconds at the half a worker it has been getting: 200 s, not 100 / 4.
    priced = await priced_together(dict(answer), NO_READ, KINDS, book, 4, True)
    assert (priced["generate"].quick_seconds, priced["generate"].slow_seconds) == (125, 320)
    # Half of them waiting for their task's own run: not this pass's work.
    pooled._STEADY.forget(Family.GENERATE)
    halved = await priced_together(
        dict(answer), NO_READ, KINDS, book, 4, True, standing={"generate": 50}
    )
    assert (halved["generate"].quick_seconds, halved["generate"].slow_seconds) == (62, 160)


async def test_a_row_the_presses_alone_describe_keeps_its_own(temp_db: Database) -> None:
    book = await _book(temp_db, scan=2.0)
    answer = await priced_together(
        _answer(slow_seconds=9), WORK, KINDS, book, 4, False, {"generate"}
    )
    assert answer["generate"].slow_seconds == 9


async def test_a_sub_task_switched_off_leaves_the_pass_priced_by_what_runs(
    temp_db: Database,
) -> None:
    book = await _book(temp_db, generate=0.5)
    off = PartOfWork(type="audio_fingerprint", caption="files", done=0, total=259, on=False)
    answer = {
        "generate": _row(
            "Generate",
            ["thumbnail", "audio_fingerprint"],
            100,
            outstanding=4,
            running=4,
            parts=[off],
        )
    }
    work = {
        "audio_fingerprint": KindOfWork(done=0, outstanding=0, failed=0, waiting=259),
        **NO_READ,
    }
    priced = await priced_together(answer, work, KINDS, book, 4, True)
    assert priced["generate"].slow_seconds == 320


async def test_a_row_that_has_stopped_says_so(temp_db: Database) -> None:
    book = await _book(temp_db, scan=2.0, generate=0.5)
    run = book.open_run(Family.SCAN)
    assert run is not None and run.last_done is not None
    run.last_done -= 200
    answer = _answer()
    answer["scan"] = answer["scan"].model_copy(update={"running": 0})

    answer = await priced_together(answer, WORK, KINDS, book, 4, False)

    assert (answer["scan"].quick_seconds, answer["scan"].time_unknown) == (None, STALLED)
    # The read stopped: a pass is its own work at its own pace, with no floor from the read.
    assert await _said(temp_db) == [(None, None, 100, 1), (125, 320, 100, 0)]
    book._throughput.pop(Family.GENERATE)
    pooled._STEADY.forget(Family.GENERATE)
    stalled = _answer()
    stalled["scan"] = stalled["scan"].model_copy(update={"running": 0})
    alone = await priced_together(stalled, WORK, KINDS, book, 4, False)
    assert (alone["generate"].time_unknown, alone["generate"].at_least) == (MEASURING, False)


@pytest.mark.parametrize(
    "unpriced",
    [
        {"time_unknown": "659 wait for their task."},
        {"reason": "Waiting for quiet hours."},
        {"waiting": 0},
    ],
)
async def test_a_row_with_its_own_sentence_or_nothing_left_keeps_its_own(
    temp_db: Database, unpriced: dict[str, Any]
) -> None:
    book = await _book(temp_db, scan=2.0)
    answer = _answer(quick_seconds=5, slow_seconds=9)
    answer["generate"] = answer["generate"].model_copy(update=unpriced)
    answer = await priced_together(answer, WORK, KINDS, book, 4, False)
    assert (answer["generate"].quick_seconds, answer["generate"].slow_seconds) == (5, 9)


async def test_the_shown_range_holds_until_the_figure_has_stayed_off_for_a_minute(
    temp_db: Database,
) -> None:
    book = await _book(temp_db, scan=2.0)
    first = await priced_together(_answer(), WORK, KINDS, book, 4, False)
    halved = {**WORK, "probe": KindOfWork(done=10, outstanding=4, failed=0, waiting=50)}
    again = await priced_together(_answer(), halved, KINDS, book, 4, False)
    shown, before = again["scan"].slow_seconds, first["scan"].slow_seconds
    assert shown is not None and before is not None and before - 1 <= shown <= before


ROUTER = importlib.import_module("sift.slices.media_jobs.activity_families")


def test_the_read_is_a_shares_only_while_its_own_reads_waited_most_of_a_minute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads = ROUTER.ShareWaits(ROUTER._READ_WAITS._waits)
    reads.busiest({"nas": {"remote": True, "urgent_wait_seconds": 0.0}}, 0.0)
    others = {"remote": True, "urgent_wait_seconds": 10.0, "ordinary_wait_seconds": 50.0}
    assert reads.busiest({"nas": others}, 60.0) is None, "the other work's waits are not the read's"
    assert reads.busiest({"nas": {**others, "urgent_wait_seconds": 40.0}}, 70.0) == "nas"

    monkeypatch.setattr(ROUTER.lanes, "installed", lambda: None)
    assert ROUTER._pool_bound()
    monkeypatch.setattr(ROUTER.lanes, "installed", lambda: SimpleNamespace(readings=dict))
    monkeypatch.setattr(ROUTER._READ_WAITS, "busiest", lambda *_: "nas")
    assert not ROUTER._pool_bound()
    monkeypatch.setattr(ROUTER._READ_WAITS, "busiest", lambda *_: None)
    assert ROUTER._pool_bound()
