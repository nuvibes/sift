# SPDX-License-Identifier: AGPL-3.0-or-later
"""The read and the passes after it priced together, held steady, a stopped row said, each kept."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any

import pytest

# The `ran` event lands in the workbench's table.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.db import Database
from sift.kernel.jobs.families import Family
from sift.kernel.jobs.ledger import Ledger
from sift.kernel.jobs.time_left import Steady
from sift.slices.media_jobs import pooled
from sift.slices.media_jobs.activity_wire import FamilyOfWork, KindOfWork, PartOfWork
from sift.slices.media_jobs.pooled import STALLED, WAITING_FOR_THE_SCAN, priced_together

FAMILIES = {"probe": Family.SCAN, "thumbnail": Family.GENERATE}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pooled, "_STEADY", Steady())
    monkeypatch.setattr(pooled, "_READ_SECONDS", {})


async def _book(temp_db: Database, *, read_for: float = 120.0) -> Ledger:
    """Ten pictures read at 2 s and ten made at 1 s, the read going for `read_for` seconds."""
    await temp_db.initialize_schema()
    book = Ledger(temp_db, families_of=FAMILIES)
    book.started("probe")
    run = book.open_run(Family.SCAN)
    assert run is not None
    run.began -= read_for
    for job_type, ms in (("probe", 2000), ("thumbnail", 1000)):
        for _ in range(10):
            book.finished(job_type, duration_ms=ms, ok=True, media_type="image")
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
KINDS = {"probe": {"image": 100.0}, "thumbnail": {"image": 50.0, "gif": 0.0}}


async def _said(temp_db: Database) -> list[tuple[object, ...]]:
    rows = await temp_db.fetch_all(
        "SELECT quick, slow, left, stalled FROM said_times ORDER BY slow"
    )
    return [tuple(row) for row in rows]


async def test_the_read_and_the_passes_after_it_are_priced_together_and_kept(
    temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    # One clock reading for the run's start and the pricing, so no real second slips in between.
    monkeypatch.setattr("sift.kernel.jobs.ledger.time.monotonic", lambda: 1_000_000.0)
    book = await _book(temp_db)
    answer = await priced_together(_answer(), WORK, KINDS, book, 4, False, ())

    # 200 s of reading at the 20 worker seconds a 120 s it has done: 1,200 s, the passes 1 s more.
    assert (answer["scan"].quick_seconds, answer["scan"].slow_seconds) == (750, 1920)
    assert (answer["generate"].quick_seconds, answer["generate"].slow_seconds) == (750, 1921)
    # Read against a real clock, so a slow machine adds the seconds the test itself took.
    assert pooled._READ_SECONDS[Family.SCAN] == pytest.approx(1200.0, abs=2.0)
    assert await _said(temp_db) == [(750, 1920, 100, 0), (750, 1921, 100, 0)]


async def test_a_pass_the_scan_holds_starts_when_the_read_is_done(temp_db: Database) -> None:
    book = await _book(temp_db)
    held = _answer(reason=WAITING_FOR_THE_SCAN, running=0)
    answer = await priced_together(held, WORK, KINDS, book, 4, False, ())
    # The read's 1,200 s and then 100 thumbnails at 1 s over 4 workers.
    assert answer["generate"].slow_seconds == int(1225 * 1.6)


async def test_a_row_that_has_stopped_says_so_and_the_passes_are_not_chained_to_it(
    temp_db: Database,
) -> None:
    book = await _book(temp_db)
    run = book.open_run(Family.SCAN)
    assert run is not None and run.last_done is not None
    run.last_done -= 200
    answer = _answer()
    answer["scan"] = answer["scan"].model_copy(update={"running": 0})

    answer = await priced_together(answer, WORK, KINDS, book, 4, False, ())

    assert (answer["scan"].quick_seconds, answer["scan"].time_unknown) == (None, STALLED)
    # Every file's work over the pool: (200 + 100) / 4.
    assert answer["generate"].slow_seconds == int(75 * 1.6)
    assert await _said(temp_db) == [(None, None, 100, 1), (46, 120, 100, 0)]


@pytest.mark.parametrize(
    "unpriced",
    [
        {"for_task": "659 wait for their task."},
        {"time_unknown": "Not enough to say yet"},
        {"reason": "Waiting for quiet hours."},
        {"waiting": 0},
    ],
)
async def test_a_row_with_its_own_sentence_or_nothing_left_keeps_its_own_estimate(
    temp_db: Database, unpriced: dict[str, Any]
) -> None:
    book = await _book(temp_db)
    answer = _answer(quick_seconds=5, slow_seconds=9)
    answer["generate"] = answer["generate"].model_copy(update=unpriced)
    answer = await priced_together(answer, WORK, KINDS, book, 4, False, ())
    assert (answer["generate"].quick_seconds, answer["generate"].slow_seconds) == (5, 9)


async def test_a_pass_by_presses_alone_and_a_kind_with_no_price_keep_their_own(
    temp_db: Database,
) -> None:
    book = await _book(temp_db)
    alone = await priced_together(
        _answer(slow_seconds=9), WORK, KINDS, book, 4, False, {"generate"}
    )
    assert alone["generate"].slow_seconds == 9
    unpriced = {"preview": KindOfWork(done=0, outstanding=0, failed=0, waiting=5), **WORK}
    answer = _answer(slow_seconds=9)
    answer["generate"] = answer["generate"].model_copy(update={"types": ["preview"]})
    answer = await priced_together(answer, unpriced, {}, book, 4, False, ())
    # A kind nothing priced: no sooner than the read and what is queued after it.
    assert answer["generate"].slow_seconds == int(1200 * 1.6)


async def test_with_no_read_left_the_passes_take_their_work_over_the_pool(
    temp_db: Database,
) -> None:
    book = await _book(temp_db)
    answer = {"generate": _row("Generate", ["thumbnail", "carrying"], 100, outstanding=4)}
    answer = await priced_together(answer, WORK, KINDS, book, 4, True, ())
    assert answer["generate"].slow_seconds == int(25 * 1.6)


async def test_a_sub_task_switched_off_with_files_waiting_leaves_the_passes_priced(
    temp_db: Database,
) -> None:
    book = await _book(temp_db)
    off = PartOfWork(type="audio_fingerprint", caption="files", done=0, total=259, on=False)
    answer = {
        "generate": _row("Generate", ["thumbnail"], 100, outstanding=4),
        "fingerprint": _row("Fingerprint", ["audio_fingerprint"], 0, parts=[off]),
    }
    work = {"audio_fingerprint": KindOfWork(done=0, outstanding=0, failed=0, waiting=259), **WORK}
    answer = await priced_together(answer, work, KINDS, book, 4, True, ())
    assert answer["generate"].slow_seconds == int(25 * 1.6)


async def test_a_read_with_no_pace_yet_leaves_every_row_its_own(temp_db: Database) -> None:
    book = await _book(temp_db, read_for=0.0)
    answer = await priced_together(_answer(slow_seconds=9), WORK, KINDS, book, 4, True, ())
    assert (answer["scan"].slow_seconds, answer["generate"].slow_seconds) == (None, 9)
    assert pooled._READ_SECONDS == {}


async def test_the_shown_range_holds_until_the_figure_has_stayed_off_for_a_minute(
    temp_db: Database,
) -> None:
    book = await _book(temp_db)
    first = await priced_together(_answer(), WORK, KINDS, book, 4, False, ())
    halved = {**WORK, "probe": KindOfWork(done=10, outstanding=4, failed=0, waiting=50)}
    again = await priced_together(_answer(), halved, KINDS, book, 4, False, ())
    shown, before = again["scan"].slow_seconds, first["scan"].slow_seconds
    assert shown is not None and before is not None and before - 1 <= shown <= before


ROUTER = importlib.import_module("sift.slices.media_jobs.router")


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
