# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ledger: one run per family, what it cost, and an estimate with memory.

Against a real database, because the run is written through and read back, and because the
estimate's memory is a row from a previous run.
"""

from __future__ import annotations

import dataclasses
import json
import time

import pytest

# The `ran` event lands in the workbench's table, which the workbench slice registers.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.db import Database
from sift.kernel.jobs import ledger as ledger_module
from sift.kernel.jobs.families import LONG_PASSES, Family
from sift.kernel.jobs.ledger import CURRENT_FAMILY, Ledger, priced, report_text

FAMILIES = {
    "scan": Family.SCAN,
    "probe": Family.SCAN,
    "thumbnail": Family.GENERATE,
    "face_scan": Family.IDENTIFY,
    "identify": Family.IDENTIFY,
    "backup_run": Family.OTHER,
}


@pytest.fixture
async def ledger(temp_db: Database) -> Ledger:
    await temp_db.initialize_schema()
    book = Ledger(temp_db, machine="a box", profile="abc123", version="0.1.0", families_of=FAMILIES)
    await book.start()
    return book


async def test_a_run_opens_with_the_first_job_and_closes_when_the_family_drains(
    ledger: Ledger,
) -> None:
    ledger.started("probe")
    ledger.finished("probe", duration_ms=1500, ok=True, media_type="video", size_bytes=10)
    ledger.finished("probe", duration_ms=500, ok=False, media_type="image", size_bytes=4)
    assert ledger.open_run(Family.SCAN) is not None

    await ledger.settle({"probe": 3}, settings={"jobs at once": 8})
    assert ledger.open_run(Family.SCAN) is not None, "still busy: nothing should close"

    await ledger.settle({"probe": 0, "thumbnail": 2}, settings={"jobs at once": 8})
    assert ledger.open_run(Family.SCAN) is None

    (run,) = await ledger.recent()
    assert run.family == "scan"
    assert run.jobs_done == 1 and run.jobs_failed == 1
    assert run.files == {
        "video": {"n": 1, "bytes": 10, "ms": 1500},
        "image": {"n": 1, "bytes": 4, "ms": 500},
    }
    assert run.worker_ms == 2000
    assert run.settings == {"jobs at once": 8}
    assert run.machine == "a box" and run.profile == "abc123" and run.version == "0.1.0"
    assert run.finished_at is not None and not run.stopped


async def test_housekeeping_is_a_run_and_is_not_a_pass(ledger: Ledger) -> None:
    """A backup, a download and a transcode are work this install did, so they leave a run, under
    `other`, and `other` is not in `LONG_PASSES`, so no screen draws it as a pass over the
    library."""
    ledger.finished("backup_run", duration_ms=10, ok=True)
    await ledger.settle({}, settings={})

    (run,) = await ledger.recent()
    assert run.family == Family.OTHER.value
    assert Family.OTHER not in LONG_PASSES


async def test_a_stage_is_filed_against_the_family_of_the_job_running_it(ledger: Ledger) -> None:
    ledger.started("probe")
    token = CURRENT_FAMILY.set(Family.SCAN)
    try:
        ledger.stage("probe.fingerprint", 120.0)
        ledger.stage("probe.fingerprint", 80.0)
        ledger.stage("db.read", 5.0)  # every statement is timed; not the work
    finally:
        CURRENT_FAMILY.reset(token)
    ledger.stage("probe.metadata", 9.0)  # outside any job: nowhere to file it
    ledger.finished("probe", duration_ms=300, ok=True)
    await ledger.settle({}, settings={})

    (run,) = await ledger.recent()
    assert run.stages == {"probe.fingerprint": {"n": 2, "ms": 200}}


async def test_a_run_stopped_by_hand_says_so(ledger: Ledger) -> None:
    ledger.started("face_scan")
    ledger.stopped_by_hand()
    await ledger.settle({}, settings={})
    (run,) = await ledger.recent()
    assert run.stopped


async def test_a_single_cancel_stops_only_the_runs_of_what_it_cancelled(ledger: Ledger) -> None:
    """Cancelling one Identify pass says nothing about a Generate run beside it."""
    ledger.started("face_scan")
    ledger.started("thumbnail")
    ledger.stopped_by_hand({"identify"})
    await ledger.settle({}, settings={})
    stopped = {run.family: run.stopped for run in await ledger.recent()}
    assert stopped == {Family.IDENTIFY.value: True, Family.GENERATE.value: False}


async def test_a_run_left_open_by_a_process_that_stopped_is_closed_at_the_next_start(
    temp_db: Database,
) -> None:
    await temp_db.initialize_schema()
    book = Ledger(temp_db, families_of=FAMILIES)
    book.started("probe")
    book.finished("probe", duration_ms=10, ok=True)
    run = book.open_run(Family.SCAN)
    assert run is not None
    await book._write(run, int(time.time()))  # the minute-by-minute write-through

    again = Ledger(temp_db, families_of=FAMILIES)
    await again.start()
    (row,) = await again.recent()
    assert row.finished_at is not None and row.stopped


async def test_a_job_about_many_files_counts_as_that_many(ledger: Ledger) -> None:
    """A scan holding thousands of files is one job and thousands of files; the run counts the
    files.

    """
    ledger.finished("scan", duration_ms=1000.0, ok=True, units=174)
    run = ledger._open[Family.SCAN]
    assert run.jobs_done == 1
    assert run.files_total == 174


async def _benchmark_prices() -> dict[str, float]:
    return {"identify": 12.0}


async def test_with_no_history_the_benchmarks_price_is_a_floor(ledger: Ledger) -> None:
    ledger.first_prices = _benchmark_prices
    alone = await ledger.estimate(Family.IDENTIFY, ["face_scan"], left=100, at_once=4)
    assert alone is not None and alone.floor
    assert (alone.quick_seconds, alone.slow_seconds, alone.items) == (300, 300, 0)

    mixed = {"video": 25.0, "image": 75.0}
    by_kind = await ledger.estimate(
        Family.IDENTIFY, ["face_scan"], left=100, at_once=4, kinds=mixed
    )
    assert by_kind is not None and by_kind.quick_seconds == 75, "only the videos are priced"

    stills = await ledger.estimate(
        Family.IDENTIFY, ["face_scan"], left=100, at_once=4, kinds={"image": 1.0}
    )
    assert stills is None
    assert await ledger.estimate(Family.SEMANTIC, ["embed"], left=100, at_once=4) is None


async def test_the_report_is_plain_text_a_person_can_paste(ledger: Ledger) -> None:
    ledger.started("probe")
    token = CURRENT_FAMILY.set(Family.SCAN)
    try:
        ledger.stage("probe.fingerprint", 4000.0)
    finally:
        CURRENT_FAMILY.reset(token)
    ledger.finished(
        "probe", duration_ms=5000, ok=True, media_type="video", size_bytes=2_000_000_000
    )
    ledger.finished("probe", duration_ms=400, ok=True, media_type="image", size_bytes=3_000_000)
    run = ledger.open_run(Family.SCAN)
    assert run is not None
    run.started_at -= 600
    await ledger.settle({}, settings={"jobs at once": 8, "network share reads": 2})
    (row,) = await ledger.recent()

    text = report_text(row)
    assert text.isascii()
    assert "Scan run" in text and "took 10.0 min" in text
    assert "Machine: a box" in text
    assert "Settings: jobs at once 8, network share reads 2" in text
    assert "1 image (3 MB)" in text and "1 video (2.0 GB)" in text
    assert "Jobs: 2 done, 0 failed" in text
    assert "probe.fingerprint: 4 s over 1, 4.00 s each" in text


async def test_a_run_from_years_ago_is_still_there_after_a_restart(temp_db: Database) -> None:
    """Runs are kept FOR EVER: nothing drops the machine's own record of the first import of a
    library a quarter after it happened, with nobody told."""
    await temp_db.initialize_schema()
    book = Ledger(temp_db, families_of=FAMILIES)
    book.started("probe")
    run = book.open_run(Family.SCAN)
    assert run is not None
    run.started_at = int(time.time()) - 5 * 365 * 24 * 60 * 60
    await book.settle({}, settings={})
    assert len(await book.recent()) == 1

    await Ledger(temp_db, families_of=FAMILIES).start()
    assert len(await book.recent()) == 1


async def test_a_job_in_no_family_still_leaves_a_run(temp_db: Database) -> None:
    """A backup, a download, a transcode: none of them is one of the five long passes, and each
    still leaves a record here, so the machine's own record of what it has been doing covers every
    class of work. It is coarse (one `other` run for all of them) and it is not drawn as a pass; it
    is written down."""
    await temp_db.initialize_schema()
    book = Ledger(temp_db, families_of=FAMILIES)
    book.finished("backup_run", duration_ms=1_500, ok=True, media_type="unknown", size_bytes=0)
    run = book.open_run(Family.OTHER)
    assert run is not None and run.jobs_done == 1

    await book.settle({}, settings={})
    recorded = await book.recent()
    assert [(one.family, one.jobs_done) for one in recorded] == [(Family.OTHER.value, 1)]


async def test_the_table_is_left_alone_when_a_database_already_has_it(temp_db: Database) -> None:
    """A database that has the table and its newest column gets neither the create nor the alter
    again: adding a column twice is an error, so each step decides for itself."""
    await temp_db.initialize_schema()
    async with temp_db.write() as connection:
        await ledger_module.initialize(connection, on_disk=ledger_module.VERSION)

    columns = await temp_db.fetch_all("SELECT name FROM pragma_table_info('work_runs')")
    assert [row["name"] for row in columns].count("products") == 1


async def test_the_families_are_learned_from_the_registry_once_it_is_full(
    temp_db: Database,
) -> None:
    book = Ledger(temp_db)
    assert book.family_of("probe") is Family.OTHER
    book.learn_families(FAMILIES)
    assert book.family_of("probe") is Family.SCAN


async def test_a_run_still_going_has_no_length_and_no_pace(ledger: Ledger) -> None:
    """Read back mid-run, a row has no finished time, so nothing that divides by it is said: the
    report says it is still going, and a kind counted with no files gets no per-file line."""
    ledger.finished("probe", duration_ms=10, ok=True, media_type="image", size_bytes=7, units=0)
    run = ledger.open_run(Family.SCAN)
    assert run is not None
    assert run.seconds is None
    assert run.files_total == 0 and run.bytes_total == 7
    await ledger._write(run, int(time.time()))

    assert await ledger.get("no such run") is None
    record = await ledger.get(run.id)
    assert record is not None
    assert record.seconds is None
    assert record.jobs_per_minute is None and record.files_per_minute is None

    text = report_text(record)
    assert "took still going" in text
    assert "0 image (7 bytes)" in text
    assert "Settings:" not in text and "Pace:" not in text
    assert "Per image" not in text and "Stages" not in text and "Built" not in text


async def test_a_finished_run_reports_its_pace_in_jobs_and_in_files(ledger: Ledger) -> None:
    ledger.finished("scan", duration_ms=10, ok=True, units=6)
    ledger.finished("scan", duration_ms=10, ok=True, units=6)
    run = ledger.open_run(Family.SCAN)
    assert run is not None
    run.started_at -= 120
    await ledger.settle({}, settings={})

    (record,) = await ledger.recent()
    assert record.jobs_per_minute == pytest.approx(1.0)
    assert record.files_per_minute == pytest.approx(6.0)


@pytest.mark.parametrize(
    ("seconds", "said"), [(45, "took 45 s"), (600, "took 10.0 min"), (7200, "took 2.0 hr")]
)
async def test_the_report_says_how_long_in_the_unit_a_person_would(
    ledger: Ledger, seconds: int, said: str
) -> None:
    ledger.finished("probe", duration_ms=10, ok=True, size_bytes=2_000_000_000_000)
    run = ledger.open_run(Family.SCAN)
    assert run is not None
    run.started_at -= seconds
    await ledger.settle({}, settings={})

    (record,) = await ledger.recent()
    text = report_text(record)
    assert said in text
    assert "1 unknown (2.00 TB)" in text


async def test_a_build_run_records_what_it_made_per_product(ledger: Ledger) -> None:
    """A Build task times each product it makes as a stage named for it, and the run files that
    under the product's own name: what the next Build is priced from."""
    ledger.started("identify")
    token = CURRENT_FAMILY.set(Family.IDENTIFY)
    try:
        ledger.stage("build.pictures", 3000.0)
        ledger.built("faces", 1000.0, files=0)
    finally:
        CURRENT_FAMILY.reset(token)
    ledger.finished("identify", duration_ms=4000, ok=True)
    await ledger.settle({}, settings={})

    (record,) = await ledger.recent()
    assert record.products == {"pictures": {"n": 1, "ms": 3000}, "faces": {"n": 0, "ms": 1000}}
    assert record.stages == {"build.pictures": {"n": 1, "ms": 3000}}
    text = report_text(record)
    assert "Built (worker time):" in text
    assert "  pictures: 1 files, 3 s, 3.0 s each" in text
    assert "  faces: 0 files, 1 s" in text.splitlines()


async def test_a_product_or_stage_with_no_run_to_file_it_under_is_let_go(ledger: Ledger) -> None:
    """Outside any job there is no family; inside one whose family has no run open there is
    nowhere to write. Neither opens a run of its own."""
    ledger.built("pictures", 10.0)
    token = CURRENT_FAMILY.set(Family.IDENTIFY)
    try:
        ledger.built("pictures", 10.0)
        ledger.stage("build.faces", 10.0)
    finally:
        CURRENT_FAMILY.reset(token)
    assert ledger.open_run(Family.IDENTIFY) is None
    await ledger.settle({}, settings={})
    assert await ledger.recent() == []


async def test_the_last_run_is_the_last_that_finished_on_this_machine(ledger: Ledger) -> None:
    assert await ledger.last_run(Family.SCAN) is None
    ledger.finished("probe", duration_ms=10, ok=True)
    run = ledger.open_run(Family.SCAN)
    assert run is not None
    await ledger.settle({}, settings={})

    last = await ledger.last_run(Family.SCAN)
    assert last is not None and last.id == run.id


async def test_a_busy_run_is_written_through_once_a_minute_and_not_on_every_tick(
    ledger: Ledger,
) -> None:
    ledger.started("probe")
    run = ledger.open_run(Family.SCAN)
    assert run is not None
    run.last_flushed = time.monotonic()  # as if written a moment ago
    await ledger.settle({"probe": 1}, settings={})
    assert await ledger.recent() == []

    run.last_flushed = 0.0
    await ledger.settle({"probe": 1}, settings={})
    (record,) = await ledger.recent()
    assert record.finished_at is None


async def test_the_end_of_a_pass_asks_for_the_statistics_to_be_refreshed(
    ledger: Ledger, temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A whole-library pass is what moves the numbers the query planner chooses plans from, and
    this is the one place that knows a pass has ENDED. Asked here rather than by each pass, so a
    pass written later is covered without anybody remembering to cover it.

    It does not ask while the family is still busy: the numbers move for as long as the pass runs,
    so a refresh in the middle of one is work thrown away.
    """
    asked: list[str] = []

    async def note(*, reason: str, every_table: bool = False, force: bool = False) -> bool:
        asked.append(reason)
        return True

    monkeypatch.setattr(temp_db, "refresh_statistics", note)

    ledger.started("probe")
    await ledger.settle({"probe": 2}, settings={})
    assert asked == [], "the pass has not finished"

    await ledger.settle({"probe": 0}, settings={})
    assert asked == ["pass:scan"]


# --- what the card was doing while this ran -----------------------------------------------------
#
# A run is compared with another run, and the numbers mean nothing without the conditions they were
# measured under. The graphics card is the largest of those conditions: a preview pass is several
# times slower without it, so two runs of one family on one machine can differ by a factor that
# nothing else in the row explains.


async def test_a_run_records_what_the_card_was_doing(temp_db: Database) -> None:
    await temp_db.initialize_schema()
    said = ["on"]
    book = Ledger(temp_db, families_of=FAMILIES, accelerator=lambda: said[0])
    await book.start()

    book.finished("thumbnail", duration_ms=10, ok=True)
    await book.settle({}, settings={})

    (row,) = await book.recent(limit=1)
    assert row.accelerator == "on"


async def test_the_answer_is_re_read_on_every_write(temp_db: Database) -> None:
    """The one value worth keeping is the one that CHANGES mid-run.

    A card is given up on partway through a pass (three refusals in a row and Sift stops asking)
    and that is exactly the moment somebody watching a pass get slower needs explained. Stamped when
    the run opened, the row would say `on` for a run that spent most of itself on the processor.
    """
    await temp_db.initialize_schema()
    said = ["on"]
    book = Ledger(temp_db, families_of=FAMILIES, accelerator=lambda: said[0])
    await book.start()

    book.finished("thumbnail", duration_ms=10, ok=True)
    said[0] = "latched_off"
    await book.settle({}, settings={})

    (row,) = await book.recent(limit=1)
    assert row.accelerator == "latched_off"


async def test_a_process_with_nothing_to_ask_claims_nothing(temp_db: Database) -> None:
    """Empty is not `off`. `off` is a claim about the machine, and a build that wires no
    accelerator at all has not made one."""
    await temp_db.initialize_schema()
    book = Ledger(temp_db, families_of=FAMILIES)
    await book.start()

    book.finished("thumbnail", duration_ms=10, ok=True)
    await book.settle({}, settings={})

    (row,) = await book.recent(limit=1)
    assert row.accelerator == ""


# --- the pace and the range, which replace the single newest run ------------------------------


async def _job_rows(
    db: Database, job_type: str, *, costs: list[int], units: int = 1, started: int = 10_000
) -> None:
    """Finished job rows costing exactly these seconds each, newest last.

    Written straight into the table rather than through the queue: the pace read is about what the
    rows SAY, and building each one through a claim and a finish would be a test of the queue.
    """
    for n, cost in enumerate(costs):
        began = started + n * 100
        await db.execute(
            "INSERT INTO jobs (id, type, state, payload, created_at, updated_at, started_at, units)"
            " VALUES (?, ?, 'done', '{}', ?, ?, ?, ?)",
            (f"{job_type}-{began}-{n}", job_type, began, began + cost, began, units),
        )


async def test_the_pace_needs_a_sample_and_a_run_of_one_file_is_not_one(
    ledger: Ledger, temp_db: Database
) -> None:
    """!! NOT THE SINGLE NEWEST FINISHED RUN OF THE FAMILY, with no minimum sample. A run is one
    family's stretch of work, so an arriving file is a run of ONE file, and that file's clock would
    price the whole library: an unchanging backlog would read days one moment and hours the next,
    with no bulk work done."""
    await _job_rows(temp_db, "face_scan", costs=[20] * 19)
    assert await ledger.pace(Family.IDENTIFY, ["face_scan"]) is None
    assert await ledger.estimate(Family.IDENTIFY, ["face_scan"], left=92168, at_once=12) is None

    await _job_rows(temp_db, "face_scan", costs=[20], started=20_000)
    found = await ledger.pace(Family.IDENTIFY, ["face_scan"])
    assert found is not None and found.items == 20 and found.from_items


async def test_the_range_is_the_cheapest_and_dearest_stretch_divided_by_the_workers(
    ledger: Ledger, temp_db: Database
) -> None:
    """Twenty items costing ten seconds each and twenty costing thirty: the cheapest stretch is ten
    a file, the dearest thirty, and forty files at twelve at once is between 33 and 100 seconds.

    Not divided by one: the price of a file is what a worker spends on it, and twelve workers
    spend it twelve at a time."""
    await _job_rows(temp_db, "face_scan", costs=[10] * 20 + [30] * 20)
    found = await ledger.pace(Family.IDENTIFY, ["face_scan"])
    assert found is not None
    assert (found.quick, found.middle, found.slow) == (10.0, 20.0, 30.0)

    estimate = await ledger.estimate(Family.IDENTIFY, ["face_scan"], left=40, at_once=12)
    assert estimate is not None
    assert estimate.at_once == 12 and estimate.items == 40
    assert estimate.quick_seconds == int(40 * 10 / 12)
    assert estimate.slow_seconds == int(40 * 30 / 12)

    alone = await ledger.estimate(Family.IDENTIFY, ["face_scan"], left=40, at_once=1)
    assert alone is not None and alone.slow_seconds == 40 * 30


async def test_a_few_dear_items_among_many_cheap_ones_are_inside_the_range(
    ledger: Ledger, temp_db: Database
) -> None:
    """What the rest costs is a SUM, and a sum lands near the mean. A stretch of long videos among
    photographs puts the mean far past the third quarter of single items, so a range quoted between
    the quarters of single items would miss every such pass on the low side."""
    await _job_rows(temp_db, "face_scan", costs=[1] * 90 + [60] * 20 + [1] * 90)
    estimate = await ledger.estimate(Family.IDENTIFY, ["face_scan"], left=200, at_once=1)
    assert estimate is not None
    total = 90 * 1 + 20 * 60 + 90 * 1
    assert estimate.quick_seconds <= total <= estimate.slow_seconds


async def test_a_young_run_is_priced_from_the_history(ledger: Ledger, temp_db: Database) -> None:
    await _job_rows(temp_db, "face_scan", costs=[6] * 40)
    ledger.started("face_scan")
    run = ledger.open_run(Family.IDENTIFY)
    assert run is not None
    estimate = await ledger.estimate(Family.IDENTIFY, ["face_scan"], left=24, at_once=12)

    assert estimate is not None
    assert (estimate.quick_seconds, estimate.slow_seconds) == (12, 12)


async def test_a_job_that_carried_many_files_is_weighed_by_them(
    ledger: Ledger, temp_db: Database
) -> None:
    """A scan's walk carries thousands of files, so its seconds divided by one would price every
    file in the library at the cost of a whole walk, and counting it as ONE observation would let
    a handful of walks outvote every file they found."""
    await _job_rows(temp_db, "scan", costs=[1000], units=100)
    found = await ledger.pace(Family.SCAN, ["scan"])
    assert found is not None
    assert found.items == 100, "one row, a hundred files"
    assert found.middle == 10.0, "ten seconds a file, not a thousand"


async def test_a_settings_change_ends_the_sample(ledger: Ledger, temp_db: Database) -> None:
    """A pace measured under the old numbers is not this machine's pace. The moment is noted on
    the pool's own tick, which is the one place the live settings arrive."""
    now = int(time.time())
    await _job_rows(temp_db, "face_scan", costs=[20] * 30, started=now - 10_000)
    assert await ledger.pace(Family.IDENTIFY, ["face_scan"]) is not None

    await ledger.settle({}, settings={"jobs at once": 8})
    await ledger.settle({}, settings={"jobs at once": 12})
    assert await ledger.pace(Family.IDENTIFY, ["face_scan"]) is None


async def test_work_costing_under_a_second_is_priced_from_the_runs_instead(
    ledger: Ledger, temp_db: Database
) -> None:
    """The job clock is whole SECONDS, so a hundred thousand thumbnails measured at nought is no
    time at all, worse than saying nothing. The run record's worker time is to the millisecond
    and stands in for exactly that case."""
    await _job_rows(temp_db, "thumbnail", costs=[0] * 40)
    assert await ledger.pace(Family.GENERATE, ["thumbnail"]) is None

    for _ in range(50):
        ledger.finished("thumbnail", duration_ms=300, ok=True, media_type="video")
    await ledger.settle({"thumbnail": 0}, settings={})

    found = await ledger.pace(Family.GENERATE, ["thumbnail"])
    assert found is not None and not found.from_items
    assert found.middle == pytest.approx(0.3)
    assert found.items == 50


async def test_a_pass_that_finishes_records_one_ran_event_with_its_counts(
    ledger: Ledger, temp_db: Database
) -> None:
    """One `ran` event per finished pass, in the transaction that closes the row: the run as its
    subject under the family's word, Sift as the actor, the run's counts in the payload."""
    ledger.started("probe")
    ledger.finished("probe", duration_ms=10, ok=True, media_type="video", size_bytes=7, arrived=1)
    ledger.finished("probe", duration_ms=10, ok=False, media_type="image", size_bytes=3)
    await ledger.settle({"probe": 2}, settings={})
    assert await temp_db.fetch_all("SELECT id FROM workbench_decisions WHERE verb = 'ran'") == []

    await ledger.settle({"probe": 0}, settings={})

    (run,) = await ledger.recent()
    (event,) = await temp_db.fetch_all(
        "SELECT id, actor_kind, payload FROM workbench_decisions WHERE verb = 'ran'"
    )
    assert event["actor_kind"] == "sift"
    said = json.loads(event["payload"])
    assert said["family"] == "scan" and said["files"] == 2 and said["bytes"] == 10
    assert said["jobs_done"] == 1 and said["jobs_failed"] == 1
    # And how many were new to the library, which a scan's line is read for (`JobContext.arrived`).
    assert said["new"] == 1
    (subject,) = await temp_db.fetch_all(
        "SELECT kind, subject_id, name FROM workbench_decision_subjects WHERE decision_id = ?",
        (event["id"],),
    )
    assert tuple(subject) == ("run", run.id, "Scan")


async def test_a_pass_somebody_pressed_is_ran_by_them_and_kept_on_the_row(
    ledger: Ledger, temp_db: Database
) -> None:
    """A pass a person pressed names them. The run opened from a
    job nobody asked for (a file arriving) and a press landed while it was draining: the press is
    still what names the run, and a second person pressing after that does not take it over."""
    await _a_user(temp_db, "user-first")
    ledger.started("probe")
    ledger.started("scan", requested_by="user-first")
    ledger.started("scan", requested_by="user-second")
    ledger.finished("scan", duration_ms=10, ok=True)
    await ledger.settle({"scan": 0, "probe": 0}, settings={})

    (run,) = await ledger.recent()
    assert run.requested_by == "user-first"
    (event,) = await temp_db.fetch_all(
        "SELECT actor_kind, actor_id FROM workbench_decisions WHERE verb = 'ran'"
    )
    assert tuple(event) == ("user", "user-first")


async def test_a_pass_whose_presser_was_removed_is_ran_by_sift_and_still_closes(
    ledger: Ledger, temp_db: Database
) -> None:
    """The event's user is a foreign key, so a user removed during an hours-long pass would
    fail the closing transaction on every tick. The line says Sift; the row keeps the id."""
    ledger.started("scan", requested_by="user-gone")
    ledger.finished("scan", duration_ms=10, ok=True)
    await ledger.settle({"scan": 0}, settings={})

    (run,) = await ledger.recent()
    assert run.finished_at is not None and run.requested_by == "user-gone"
    (event,) = await temp_db.fetch_all(
        "SELECT actor_kind FROM workbench_decisions WHERE verb = 'ran'"
    )
    assert event["actor_kind"] == "sift"


async def _a_user(database: Database, user_id: str) -> None:
    await database.execute(
        "INSERT INTO users (id, username, password_hash, role, created_at)"
        " VALUES (?, ?, 'x', 'admin', 1)",
        (user_id, user_id),
    )


async def test_a_pass_nobody_pressed_is_ran_by_sift(ledger: Ledger, temp_db: Database) -> None:
    """A run fed only by jobs that carry no requester (a schedule, a file arriving) is Sift's,
    and the row says nobody rather than guessing."""
    ledger.started("thumbnail", requested_by=None)
    ledger.finished("thumbnail", duration_ms=10, ok=True)
    await ledger.settle({"thumbnail": 0}, settings={})

    (run,) = await ledger.recent()
    assert run.requested_by is None
    (event,) = await temp_db.fetch_all(
        "SELECT actor_kind, actor_id FROM workbench_decisions WHERE verb = 'ran'"
    )
    assert event["actor_kind"] == "sift"


async def test_housekeeping_that_drains_records_no_ran_event(
    ledger: Ledger, temp_db: Database
) -> None:
    """`other` is not a pass: a transcode per video opened would be a feed line per video."""
    ledger.started("backup_run")
    ledger.finished("backup_run", duration_ms=10, ok=True)
    await ledger.settle({"backup_run": 0}, settings={})

    assert await ledger.recent()
    assert await temp_db.fetch_all("SELECT id FROM workbench_decisions WHERE verb = 'ran'") == []


async def test_a_run_before_products_were_kept_answers_by_family_and_is_not_migrated(
    temp_db: Database,
) -> None:
    """A run records which products it was for (`made_for`). Runs from before that are left as
    they were (NULL, not an empty list), and still answer for their family, while a run known to
    have been for other products does not."""
    await temp_db.initialize_schema()
    await temp_db.execute(
        "INSERT INTO work_runs (id, family, started_at, updated_at, finished_at)"
        " VALUES ('01OLDRUN', 'identify', 100, 110, 110)"
    )
    book = Ledger(temp_db, families_of=FAMILIES, products_of={"face_scan": ("faces",)})
    book.started("face_scan")
    book.finished("face_scan", duration_ms=5.0, ok=True)
    await book.settle({}, settings={})

    faces = await book.last_run_for(["faces"], Family.IDENTIFY)
    watermarks = await book.last_run_for(["watermarks"], Family.IDENTIFY)
    assert faces is not None and faces.made_for == ("faces",)
    assert watermarks is not None and watermarks.id == "01OLDRUN", "the old run, by family"
    assert watermarks.made_for is None, "not migrated"


async def test_a_runs_line_is_named_after_the_task_it_was_for(temp_db: Database) -> None:
    """Music's press is a Generate-family run, and its line must not read "You ran Generate in 6 s:
    192 files". A run is named after the task its products belong to, by the task registry's title;
    one made for several tasks' products after the one pressed, else by its family; one that
    recorded no product (as an older run did not), by family."""
    import sift.main  # noqa: F401 (every task declared, so the registry has their titles)
    from sift.kernel.jobs.schedules import get_schedule

    await temp_db.initialize_schema()
    book = Ledger(
        temp_db,
        families_of={
            "generate_file": Family.GENERATE,
            "thumbnail": Family.GENERATE,
            "identify_file": Family.IDENTIFY,
            "watermark_read": Family.IDENTIFY,
        },
        products_of={"watermark_read": ("watermarks",)},
    )
    book.learn_tasks({"music": ("music",), "faces": ("faces",), "watermarks": ("watermarks",)})

    async def named() -> str:
        rows = await temp_db.fetch_all(
            "SELECT s.name FROM workbench_decision_subjects s"
            " JOIN workbench_decisions d ON d.id = s.decision_id"
            " WHERE d.verb = 'ran' ORDER BY d.id DESC LIMIT 1"
        )
        return str(rows[0]["name"])

    def title(task_id: str) -> str:
        task = get_schedule(task_id)
        assert task is not None
        return task.title

    book.started("generate_file", products=["music"])
    await book.settle({}, settings={})
    assert await named() == title("music"), "Music's press is Music's line, not Generate's"

    book.started("identify_file", requested_by="acct-1", products=["faces"])
    book.started("watermark_read")
    await book.settle({}, settings={})
    assert await named() == title("faces"), "a shared run is named after the task pressed"

    book.started("identify_file", products=["faces"])
    book.started("watermark_read")
    await book.settle({}, settings={})
    assert await named() == "Identify", "nobody pressed either: its family, as before"

    book.started("thumbnail")
    await book.settle({}, settings={})
    assert await named() == "Generate", "a run with no product recorded keeps its family"


# --- priced by media kind ---------------------------------------------------------------------


async def _run_row(
    db: Database,
    *,
    files: dict[str, tuple[int, int]],
    seconds: int,
    started: int,
    stopped: bool = False,
) -> None:
    """A finished Generate run written straight into the table; `files` is kind -> (items, ms)."""
    await db.execute(
        "INSERT INTO work_runs (id, family, started_at, updated_at, finished_at, stopped,"
        " jobs_done, worker_ms, files, profile) VALUES (?, 'generate', ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            f"run-{started}",
            started,
            started + seconds,
            started + seconds,
            int(stopped),
            sum(n for n, _ms in files.values()),
            sum(ms for _n, ms in files.values()),
            json.dumps({kind: {"n": n, "bytes": 0, "ms": ms} for kind, (n, ms) in files.items()}),
            "abc123",
        ),
    )


async def test_a_run_over_videos_is_not_priced_from_a_run_over_photos(
    ledger: Ledger, temp_db: Database
) -> None:
    """Photographs at a twentieth of a second from the newest run, videos at six seconds from a
    stopped one before it: each kind at its own price, a mix by its shares."""
    await _run_row(temp_db, files={"video": (30, 180_000)}, seconds=20, started=1_000, stopped=True)
    await _run_row(temp_db, files={"image": (200, 10_000)}, seconds=2, started=2_000)

    async def priced(kinds: dict[str, float]) -> tuple[int, int]:
        found = await ledger.estimate(
            Family.GENERATE, ["thumbnail"], left=40, at_once=4, kinds=kinds
        )
        assert found is not None
        return found.quick_seconds, found.slow_seconds

    assert await priced({"video": 40}) == (60, 60)
    assert (await priced({"image": 40}))[1] <= 1
    assert (await priced({"video": 10, "image": 30}))[0] == int(40 * (0.25 * 6 + 0.75 * 0.05) / 4)


def test_a_priced_range_always_holds_its_own_mean() -> None:
    """Twenty cheap items make the one whole stretch; the seven dear ones behind them are too few
    for a stretch of their own and still carry the mean to eight and a half. A range of one second
    to one second would leave its own middle out, so the slow end is the mean."""
    found = priced([(1.0, 20), (30.0, 7)])
    assert found is not None
    assert found.middle == pytest.approx(230 / 27)
    assert found.quick == 1.0 and found.slow == pytest.approx(found.middle)
    assert priced([(1.0, 19)]) is None


async def test_a_kind_nothing_has_priced_leaves_the_whole_unsaid(
    ledger: Ledger, temp_db: Database
) -> None:
    await _run_row(temp_db, files={"video": (30, 180_000)}, seconds=20, started=1_000)
    assert (
        await ledger.estimate(
            Family.GENERATE, ["thumbnail"], left=10, at_once=4, kinds={"video": 5, "gif": 5}
        )
        is None
    )


async def test_the_slow_end_divides_by_the_workers_the_runs_kept_busy(
    ledger: Ledger, temp_db: Database
) -> None:
    """Sixty videos of six worker seconds each took ninety seconds on the clock: four workers busy,
    not the twelve the pool has. The quick end assumes twelve, the slow end four."""
    await _run_row(temp_db, files={"video": (60, 360_000)}, seconds=90, started=1_000)
    found = await ledger.estimate(
        Family.GENERATE, ["thumbnail"], left=60, at_once=12, kinds={"video": 60}
    )
    assert found is not None
    assert (found.quick_seconds, found.slow_seconds) == (30, 90)


# --- the smaller answers the estimate is built from ---------------------------------------------


async def test_a_pace_asked_for_no_job_types_is_read_from_the_runs(ledger: Ledger) -> None:
    for _ in range(50):
        ledger.finished("thumbnail", duration_ms=300, ok=True, media_type="video")
    await ledger.settle({"thumbnail": 0}, settings={})

    found = await ledger.pace(Family.GENERATE, [])

    assert found is not None and not found.from_items
    assert found.items == 50


async def test_runs_from_before_a_settings_change_or_that_timed_nothing_price_nothing(
    ledger: Ledger,
) -> None:
    """A run under the old numbers is not this machine's pace now, and a run that finished no file
    or spent no measured time has no price per file to give."""
    ledger.started("thumbnail")
    older = ledger.open_run(Family.GENERATE)
    assert older is not None
    older.started_at -= 100
    for _ in range(50):
        ledger.finished("thumbnail", duration_ms=300, ok=True, media_type="video")
    await ledger.settle({}, settings={"jobs at once": 8})
    await ledger.settle({}, settings={"jobs at once": 12})
    ledger.finished("thumbnail", duration_ms=300, ok=True, media_type="video", units=0)
    await ledger.settle({}, settings={"jobs at once": 12})
    # Between the two runs, whichever way the machine's clock stepped.
    ledger._settings_changed_at = older.started_at + 1

    assert await ledger.pace(Family.GENERATE, []) is None


async def test_each_kind_is_priced_from_the_newest_runs_that_did_enough_of_it(
    ledger: Ledger,
) -> None:
    """The newest run's two hundred and fifty videos are enough on their own, so the older run's
    are not read; a kind a run counted and did none of is not a price; and runs too small to fill
    the workers say nothing about how many the work keeps busy."""
    ledger.finished("thumbnail", duration_ms=5_000, ok=True, media_type="video", units=10)
    older = ledger.open_run(Family.GENERATE)
    assert older is not None
    # Earlier by a clear margin: a machine's clock can step backwards, and "newest" is by start.
    older.started_at -= 1000
    await ledger.settle({}, settings={})
    ledger.finished("thumbnail", duration_ms=250_000, ok=True, media_type="video", units=250)
    ledger.finished("thumbnail", duration_ms=10, ok=True, media_type="image", units=0)
    await ledger.settle({}, settings={})

    prices = await ledger.kind_prices(Family.GENERATE, at_once=200)

    assert set(prices.paces) == {"video"}
    assert prices.paces["video"].items == 250
    assert prices.busy is None


async def test_a_kind_keeps_its_price_however_many_runs_of_other_kinds_follow(
    ledger: Ledger,
) -> None:
    ledger.finished("thumbnail", duration_ms=20_000, ok=True, media_type="gif", units=20)
    older = ledger.open_run(Family.GENERATE)
    assert older is not None
    older.started_at -= 1000
    await ledger.settle({}, settings={})
    for _ in range(ledger_module.KIND_OVER_RUNS + 1):
        ledger.finished("thumbnail", duration_ms=1_000, ok=True, media_type="video", units=1)
        await ledger.settle({}, settings={})

    prices = await ledger.kind_prices(Family.GENERATE, at_once=1, kinds=["gif", "video"])
    found = await ledger.estimate(
        Family.GENERATE, ["thumbnail"], left=10, at_once=1, kinds={"gif": 10}
    )

    assert prices.paces["gif"].items == 20
    assert prices.paces["video"].items == ledger_module.KIND_OVER_RUNS
    assert found is not None and found.quick_seconds == 10


async def test_a_runs_report_says_what_its_failed_jobs_ended_with(ledger: Ledger) -> None:
    gone = "The folder stopped answering partway through the scan, so nothing in it was marked."
    ledger.finished("scan", duration_ms=3_000, ok=False, units=9, failed_with=gone)
    for name in ("one.mp4", "two.mp4"):
        ledger.finished("probe", duration_ms=10, ok=False, failed_with=f"FileNotFoundError: {name}")
    ledger.finished("probe", duration_ms=10, ok=False)
    await ledger.settle({}, settings={})

    (run,) = await ledger.recent(limit=1)
    said = report_text(run).splitlines()

    assert said[said.index("Jobs: 0 done, 4 failed") + 1 :][:2] == [
        "Why 2 failed: A file it needed wasn't there. It may have been moved or deleted, or its"
        " drive isn't connected.",
        "Why 1 failed: A folder stopped answering partway through the scan. Scan it again once"
        " it's back.",
    ]


async def test_a_walks_report_says_no_kind_or_size_it_never_learned(ledger: Ledger) -> None:
    ledger.finished("scan", duration_ms=7_000, ok=False, units=168, failed_with="gone")
    ledger.finished("probe", duration_ms=40, ok=True, media_type="image", size_bytes=1_000_000)
    await ledger.settle({}, settings={})

    (run,) = await ledger.recent(limit=1)
    said = report_text(run)

    assert "Files: 1 image (1 MB)" in said
    assert "unknown" not in said and "0 bytes" not in said


async def test_a_done_scan_that_left_a_folder_unread_says_so_in_its_report(
    ledger: Ledger,
) -> None:
    unread = (
        "1 folder stopped answering partway through, so nothing in it was marked missing or"
        " unreadable."
    )
    ledger.finished("scan", duration_ms=3_000, ok=True, units=48, noted=unread)
    ledger.finished("scan", duration_ms=3_000, ok=True, units=9, noted="9 files to read.")
    run = ledger.open_run(Family.SCAN)
    assert run is not None
    run.started_at -= 60
    await ledger.settle({}, settings={})

    (record,) = await ledger.recent(limit=1)
    said = report_text(record).splitlines()

    assert said[said.index("Jobs: 2 done, 0 failed") + 1 :][:2] == [
        f"Ended with: {unread}",
        "Pace: 2.0 jobs/min",
    ]
    assert not any("files to read" in line for line in said)
    assert "Files:" not in "\n".join(said) and "bytes/min" not in "\n".join(said)


@pytest.mark.integration
async def test_a_ledger_from_before_the_endings_gets_the_column_and_keeps_its_runs(
    temp_db: Database,
) -> None:
    before = ledger_module._CREATE_TABLE.split("  made_for     TEXT,")[0] + "  made_for     TEXT\n)"
    assert "ended_with" not in before
    async with temp_db.write() as connection:
        await connection.execute("DROP TABLE IF EXISTS work_runs")
        await connection.execute(before)  # nosemgrep: sift-no-string-built-sql
        await connection.execute(
            "INSERT INTO work_runs (id, family, started_at, updated_at, finished_at, jobs_failed)"
            " VALUES ('R1', 'scan', 1, 2, 2, 1)"
        )
        await ledger_module.initialize(connection, on_disk=5)

    record = await Ledger(temp_db).get("R1")
    assert record is not None and record.jobs_failed == 1 and record.ended_with == {}
    assert "Why" not in report_text(record)


async def test_nothing_left_is_no_estimate(ledger: Ledger) -> None:
    assert await ledger.estimate(Family.GENERATE, ["thumbnail"], left=0, at_once=4) is None


def test_estimate_the_history_floor_stays_twenty() -> None:
    """Nineteen priced files are no pace."""
    assert ledger_module.FEWEST_ITEMS == 20
    assert priced([(1.0, 19)]) is None
    assert priced([(1.0, 20)]) is not None


async def test_only_the_runs_on_record_can_be_reported_on(ledger: Ledger) -> None:
    ledger.finished("probe", duration_ms=10, ok=True)
    await ledger.settle({}, settings={})
    (run,) = await ledger.recent()

    assert await ledger.recorded_among([]) == set()
    assert await ledger.recorded_among([run.id, "tidy", run.id]) == {run.id}


async def test_a_run_of_a_family_since_retired_is_reported_under_the_name_it_was_written_with(
    ledger: Ledger,
) -> None:
    """A row outlives the code that wrote it: the report carries the stored word rather than
    refusing the run."""
    from dataclasses import replace

    ledger.finished("probe", duration_ms=10, ok=True)
    await ledger.settle({}, settings={})
    (record,) = await ledger.recent()

    text = report_text(replace(record, family="retired_pass"))

    assert "retired_pass run" in text


@pytest.mark.parametrize(
    ("sample", "at", "cost"),
    [
        ([(3.0, 1)], 0.5, 3.0),
        ([(1.0, 3), (9.0, 1)], 0.5, 1.0),
        ([(9.0, 1), (1.0, 1)], 1.0, 9.0),
        ([(1.0, 1), (9.0, 100)], 0.5, 9.0),
    ],
)
def test_a_quantile_is_a_cost_that_was_measured_weighted_by_items(
    sample: list[tuple[float, int]], at: float, cost: float
) -> None:
    assert ledger_module._quantile(sample, at) == cost


async def test_a_run_mostly_stepped_back_is_kept_with_its_seconds_and_not_priced_from(
    ledger: Ledger,
) -> None:
    """Its pace was a share's, so a price from it would read every later estimate dearer."""
    await ledger.settle({}, settings={}, stepped_back=True)
    ledger.started("thumbnail")
    shared = ledger.open_run(Family.GENERATE)
    assert shared is not None
    shared.started_at -= 100
    shared.began -= 100
    assert ledger._ticked is not None
    ledger._ticked -= 80
    for _ in range(50):
        ledger.finished("thumbnail", duration_ms=300, ok=True, media_type="video")
    await ledger.settle({}, settings={"jobs at once": 8}, stepped_back=False)

    (kept,) = await ledger.recent()
    assert kept.settings["jobs at once"] == 8
    assert 79 <= int(kept.settings[ledger_module.STEPPED_BACK]) <= 81  # type: ignore[call-overload]
    assert await ledger.pace(Family.GENERATE, []) is None
    assert (await ledger.kind_prices(Family.GENERATE, at_once=1)).paces == {}

    for _ in range(50):
        ledger.finished("thumbnail", duration_ms=300, ok=True, media_type="video")
    await ledger.settle({}, settings={"jobs at once": 8})
    found = await ledger.pace(Family.GENERATE, [])
    assert found is not None and found.items == 50, "a run at the full count is priced"


def _last_end(ledger: Ledger) -> int | None:
    return ledger._spans[-1][1]


async def test_jobs_inside_a_stepped_back_stretch_are_left_out_of_the_price(
    ledger: Ledger, temp_db: Database
) -> None:
    await ledger.settle({}, settings={}, stepped_back=True)
    assert _last_end(ledger) is None, "a stretch is open while the work is stepped back"
    await ledger.settle({}, settings={}, stepped_back=False)
    assert _last_end(ledger) is not None
    ledger._spans.append([10_000, 11_000])
    await _job_rows(temp_db, "face_scan", costs=[20] * 20)
    assert await ledger.pace(Family.IDENTIFY, ["face_scan"]) is None, "only ten are full speed"
    await _job_rows(temp_db, "face_scan", costs=[20] * 10, started=20_000)
    found = await ledger.pace(Family.IDENTIFY, ["face_scan"])
    assert found is not None and found.items == 20


async def test_a_type_and_kind_is_priced_from_ten_items_and_a_settings_change_forgets_it(
    ledger: Ledger,
) -> None:
    for _ in range(9):
        ledger.finished("thumbnail", duration_ms=2000, ok=True, media_type="video")
    ledger.finished("thumbnail", duration_ms=9000, ok=False, media_type="video")
    ledger.finished("scan", duration_ms=9000, ok=True, units=0)
    assert ledger.prices() == {}
    ledger.finished("thumbnail", duration_ms=4000, ok=True, media_type="video", units=4)
    assert ledger.prices() == {("thumbnail", "video"): 1.9, ("thumbnail", ""): 1.9}

    await ledger.settle({"thumbnail": 1}, settings={"jobs at once": 8})
    await ledger.settle({"thumbnail": 1}, settings={"jobs at once": 4})
    assert ledger.prices() == {}


def _gone(ledger: Ledger, family: Family, seconds: float) -> None:
    run = ledger.open_run(family)
    assert run is not None
    run.began -= seconds


async def test_a_runs_realized_pace_is_its_priced_work_over_its_unpaused_minutes(
    ledger: Ledger,
) -> None:
    prices = {("probe", "image"): 2.0, ("probe", ""): 3.0}
    assert ledger.realized(Family.SCAN, prices, 600) is None
    ledger.started("probe")
    assert ledger.realized(Family.SCAN, prices, 600) is None, "under a minute is no pace"
    _gone(ledger, Family.SCAN, 120)
    assert ledger.realized(Family.SCAN, prices, 600) is None, "nothing done is no pace"
    for kind in ("image", "image", "video"):
        ledger.finished("probe", duration_ms=1000, ok=True, media_type=kind)
    assert ledger.realized(Family.SCAN, prices, 600) == pytest.approx(7 / 120, rel=0.01)
    assert ledger.realized(Family.SCAN, prices, 70) == pytest.approx(7 / 60, rel=0.02)
    assert ledger.realized(Family.SCAN, {}, 600) is None


async def test_time_stepped_back_is_not_read_as_pace(ledger: Ledger) -> None:
    ledger.started("probe")
    _gone(ledger, Family.SCAN, 240)
    ledger.finished("probe", duration_ms=1000, ok=True, media_type="image")
    await ledger.settle({"probe": 1}, settings={}, stepped_back=True)
    assert ledger._ticked is not None
    ledger._ticked -= 120
    await ledger.settle({"probe": 1}, settings={}, stepped_back=False)
    pace = ledger.realized(Family.SCAN, {("probe", "image"): 1.0}, 600)
    assert pace == pytest.approx(1 / 120, rel=0.02)


async def test_a_run_has_stopped_for_as_long_as_since_its_last_job_or_its_start(
    ledger: Ledger,
) -> None:
    assert ledger.stopped_for(Family.SCAN) is None
    assert ledger.life(Family.SCAN) is None
    ledger.started("probe")
    _gone(ledger, Family.SCAN, 300)
    stopped = ledger.stopped_for(Family.SCAN)
    assert stopped is not None and stopped >= 300
    assert (ledger.life(Family.SCAN) or 0) >= 300
    ledger.finished("probe", duration_ms=1000, ok=False)
    assert (ledger.stopped_for(Family.SCAN) or 0) < 1


async def _said(temp_db: Database) -> list[tuple[object, ...]]:
    rows = await temp_db.fetch_all("SELECT quick, slow, low, high, left, stalled FROM said_times")
    return [tuple(row) for row in rows]


async def test_a_live_run_keeps_what_it_said_once_a_minute(
    ledger: Ledger, temp_db: Database
) -> None:
    await ledger.said(Family.SCAN, 60, 100, (0, 300), 40)
    assert await _said(temp_db) == [], "no run open, nothing to keep"
    ledger.started("probe")
    await ledger.said(Family.SCAN, 60, 100, (0, 300), 40)
    await ledger.said(Family.SCAN, 50, 90, (0, 300), 39)
    assert await _said(temp_db) == [(60, 100, 0, 300, 40, 0)]
    run = ledger.open_run(Family.SCAN)
    assert run is not None and run.said_at is not None
    run.said_at -= 60
    await ledger.said(Family.SCAN, None, None, None, 30)
    assert (None, None, None, None, 30, 1) in await _said(temp_db)


async def test_a_run_is_scored_when_it_ends_and_its_report_says_how_right_it_was(
    ledger: Ledger, temp_db: Database
) -> None:
    ledger.started("probe")
    run = ledger.open_run(Family.SCAN)
    assert run is not None
    now = int(time.time())
    for at, low, high, stalled in (
        (now - 600, 300, 900, 0),
        (now - 300, 0, 60, 0),
        (now - 120, None, None, 1),
    ):
        await temp_db.execute(
            "INSERT INTO said_times VALUES (?, ?, NULL, NULL, ?, ?, 1, ?)",
            (run.id, at, low, high, stalled),
        )
    await ledger.settle({}, settings={})

    (record,) = await ledger.recent()
    assert record.time_left == {"minutes": 2, "right": 50, "thirds": [100, 0, None], "stalled": 1}
    assert await _said(temp_db) == []
    said = report_text(record)
    assert "Its time left was right in 50% of minutes." in said
    assert "It waited for other work for 1 minute." in said
    longer = report_text(dataclasses.replace(record, time_left={"right": 91, "stalled": 3}))
    assert "It waited for other work for 3 minutes." in longer
    unscored = report_text(dataclasses.replace(record, time_left=None))
    assert "time left" not in unscored and "other work" not in unscored


async def test_the_next_start_forgets_what_interrupted_runs_said(temp_db: Database) -> None:
    await temp_db.initialize_schema()
    await temp_db.execute("INSERT INTO said_times VALUES ('R1', 1, 1, 2, 0, 60, 1, 0)")
    await Ledger(temp_db).start()
    assert await _said(temp_db) == []


async def test_a_ledger_from_before_the_time_left_gets_its_column_and_table(
    temp_db: Database,
) -> None:
    before = ledger_module._CREATE_TABLE.split("  ended_with   TEXT,")[0] + "  ended_with   TEXT\n)"
    assert "time_left" not in before
    async with temp_db.write() as connection:
        await connection.execute("DROP TABLE IF EXISTS work_runs")
        await connection.execute("DROP TABLE IF EXISTS said_times")
        await connection.execute(before)  # nosemgrep: sift-no-string-built-sql
        await connection.execute(
            "INSERT INTO work_runs (id, family, started_at, updated_at, finished_at)"
            " VALUES ('R1', 'scan', 1, 2, 2)"
        )
        await ledger_module.initialize(connection, on_disk=6)
        await ledger_module.initialize(connection, on_disk=6)

    record = await Ledger(temp_db).get("R1")
    assert record is not None and record.time_left is None
    assert await _said(temp_db) == []
