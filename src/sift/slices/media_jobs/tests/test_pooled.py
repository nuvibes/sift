# SPDX-License-Identifier: AGPL-3.0-or-later
"""The read and the passes after it priced together at the read's measured pace, a pass with no
read ahead at its own, "Measuring." before either, held steady, a stopped row said, each kept."""

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


async def test_the_read_and_the_passes_after_it_go_at_the_reads_measured_pace(
    temp_db: Database,
) -> None:
    book = await _book(temp_db, scan=2.0)
    answer = await priced_together(_answer(), WORK, KINDS, book, 4, False)

    # 200 worker seconds of reading at 2 a second: 100 s; the passes the queued second after it.
    assert (answer["scan"].quick_seconds, answer["scan"].slow_seconds) == (62, 160)
    assert (answer["generate"].quick_seconds, answer["generate"].slow_seconds) == (63, 161)
    assert await _said(temp_db) == [(62, 160, 100, 0), (63, 161, 100, 0)]


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
    book = await _book(temp_db, scan=2.0)
    run = book.open_run(Family.SCAN)
    assert run is not None and run.last_done is not None
    run.last_done -= 200
    answer = _answer()
    answer["scan"] = answer["scan"].model_copy(update={"running": 0})

    answer = await priced_together(answer, WORK, KINDS, book, 4, False)

    assert (answer["scan"].quick_seconds, answer["scan"].time_unknown) == (None, STALLED)
    # The read stopped: the passes are their own work over the pool, 300 / 4, not after the read.
    assert await _said(temp_db) == [(None, None, 100, 1), (46, 120, 100, 0)]


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
