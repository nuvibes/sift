# SPDX-License-Identifier: AGPL-3.0-or-later
"""The run ledger's tables, its statements, and the prices kept from finished runs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Sequence

from sift.kernel.db import Connection, Row, register_schema_initializer
from sift.kernel.jobs.families import LONG_PASSES
from sift.kernel.jobs.pacing import _pace_of_runs, _price_row, priced
from sift.kernel.jobs.run_records import run_record
from sift.kernel.migrations import column_exists

COMPONENT = "ledger"
VERSION = 8

# `files` and `stages` are JSON: a run is read whole, and nothing asks for one stage across runs.
#
# `machine` is the hardware report's label, `profile` its digest (runs compare within one digest), `settings` the numbers that decided the pace.
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS work_runs (
  id           TEXT PRIMARY KEY,
  family       TEXT NOT NULL,
  started_at   INTEGER NOT NULL,
  updated_at   INTEGER NOT NULL,
  finished_at  INTEGER,
  stopped      INTEGER NOT NULL DEFAULT 0,
  jobs_done    INTEGER NOT NULL DEFAULT 0,
  jobs_failed  INTEGER NOT NULL DEFAULT 0,
  worker_ms    INTEGER NOT NULL DEFAULT 0,
  files        TEXT NOT NULL DEFAULT '{}',
  stages       TEXT NOT NULL DEFAULT '{}',
  machine      TEXT NOT NULL DEFAULT '',
  profile      TEXT NOT NULL DEFAULT '',
  settings     TEXT NOT NULL DEFAULT '{}',
  version      TEXT NOT NULL DEFAULT '',
  -- What a Build run made, per product: how many files got it and the worker time it took. What
  -- the Build sheet prices the next run from.
  products     TEXT NOT NULL DEFAULT '{}',
  -- What the graphics card was doing while this run happened: `on`, `off` or `latched_off`. The
  -- card is the largest of the conditions a run's numbers mean anything beside, and `latched_off`
  -- (the card refused three times running and Sift stopped asking) is the one that changes
  -- mid-session. Empty where the recorder did not know, which is not `off`.
  accelerator  TEXT NOT NULL DEFAULT '',
  -- The user whose press this run carried out (`jobs.requested_by`, see `Ledger.started`), or NULL
  -- for a run fed only by schedules, arriving files and a pass's own children: Sift's run.
  requested_by TEXT,
  -- Which PRODUCTS this run's work was for, as a JSON list of product keys (`faces`, `music`). A
  -- run is a FAMILY draining and a family is not a task: Faces and Watermarks are both Identify. A
  -- job says what it is for and the run keeps the union. NULL is a run that is readable by family
  -- alone (`LAST_RUN_FOR_PRODUCTS`); an empty list is a run known to have been for no product.
  made_for     TEXT,
  -- Why its jobs failed, in plain words, with how many: {"words": n}. NULL when none did.
  ended_with   TEXT,
  -- How often its time left held the real finish (`time_left.score`), or NULL.
  time_left    TEXT
)
"""

#: What a live run's row said each minute, until the run ends and is scored.
_CREATE_SAID = """
CREATE TABLE IF NOT EXISTS said_times (
  run_id  TEXT NOT NULL,
  at      INTEGER NOT NULL,
  quick   INTEGER,
  slow    INTEGER,
  low     INTEGER,
  high    INTEGER,
  left    INTEGER NOT NULL,
  stalled INTEGER NOT NULL DEFAULT 0
)
"""
_SAID_INDEX = "CREATE INDEX IF NOT EXISTS ix_said_times_run ON said_times(run_id, at)"

#: Each long pass's price on this machine, kept when one of its runs closes, so a screen reads a
#: few rows rather than the runs. `kind` is a media kind, `''` the family's pace over its runs
#: (with `runs`, the recent runs' [files, worker ms, seconds] for how busy they kept the pool) and
#: `'*'` its pace over its timed items. A NULL pace is a sample too small to quote.
_CREATE_PRICES = """
CREATE TABLE IF NOT EXISTS work_prices (
  family     TEXT NOT NULL,
  profile    TEXT NOT NULL,
  kind       TEXT NOT NULL,
  items      INTEGER,
  quick      REAL,
  middle     REAL,
  slow       REAL,
  from_items INTEGER NOT NULL DEFAULT 0,
  runs       TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY (family, profile, kind)
) WITHOUT ROWID
"""
_PRICES_OF = "SELECT * FROM work_prices WHERE family = ? AND profile = ?"
_FORGET_PRICES = "DELETE FROM work_prices WHERE family = ? AND profile = ?"
_KEEP_PRICE = (
    "INSERT OR REPLACE INTO work_prices"
    " (family, profile, kind, items, quick, middle, slow, from_items, runs)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_PRICED_PAIRS = "SELECT DISTINCT family, profile FROM work_runs WHERE finished_at IS NOT NULL"
#: The kinds a family's runs did since the settings moved: the ones it has a price for.
_KINDS_OF_FAMILY = """
SELECT DISTINCT one.key AS kind FROM work_runs w, json_each(w.files) one
 WHERE w.family = ? AND w.profile = ? AND w.finished_at IS NOT NULL AND w.started_at >= ?
   AND json_extract(one.value, '$.n') > 0
"""

_INDEXES = (
    # The screen reads the newest runs of every family; the estimate reads the newest of one.
    "CREATE INDEX IF NOT EXISTS ix_work_runs_recent ON work_runs(started_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_work_runs_family ON work_runs(family, started_at DESC)",
)


async def initialize(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        await connection.execute(_CREATE_TABLE)
        for index in _INDEXES:
            await connection.execute(index)
    if 0 < on_disk < 6 and not await column_exists(connection, "work_runs", "ended_with"):
        await connection.execute("ALTER TABLE work_runs ADD COLUMN ended_with TEXT")
    if 0 < on_disk < 7 and not await column_exists(connection, "work_runs", "time_left"):
        await connection.execute("ALTER TABLE work_runs ADD COLUMN time_left TEXT")
    await connection.execute(_CREATE_SAID)
    await connection.execute(_SAID_INDEX)
    await connection.execute(_CREATE_PRICES)
    if 0 < on_disk < 8:
        # A library from before prices were kept is priced immediately, from the runs it already has.
        passes = {family.value for family in LONG_PASSES}
        for row in await connection.execute_fetchall(_PRICED_PAIRS):
            if str(row[0]) in passes:
                rows = await _prices_from_runs(
                    connection.execute_fetchall, str(row[0]), str(row[1]), 0
                )
                await connection.executemany(_KEEP_PRICE, rows)


Fetch = Callable[[str, Sequence[object]], Awaitable[Iterable[Row]]]


async def _prices_from_runs(
    fetch: Fetch, family: str, profile: str, since: int
) -> list[tuple[object, ...]]:
    """A family's kept prices from its runs since `since`: one row per kind it did, and `''`."""
    recent = [
        one
        for one in map(
            run_record,
            await fetch(_RUNS_FOR_KINDS, (family, profile, STEPPED_BACK_PRICED, KIND_OVER_RUNS)),
        )
        if one.started_at >= since
    ]
    rows: list[tuple[object, ...]] = []
    for kind_row in await fetch(_KINDS_OF_FAMILY, (family, profile, since)):
        kind = str(kind_row[0])
        sample: list[tuple[float, int]] = []
        for run in map(
            run_record,
            await fetch(
                _RUNS_FOR_KIND,
                (family, profile, STEPPED_BACK_PRICED, since, kind, KIND_OVER_RUNS),
            ),
        ):
            n = int(run.files[kind].get("n", 0))
            if sum(weight for _cost, weight in sample) < PACE_OVER_ITEMS:
                sample.append((int(run.files[kind].get("ms", 0)) / 1000 / n, n))
        if (found := priced(sample)) is not None:
            rows.append(_price_row(family, profile, kind, found))
    last = await fetch(_LAST_RUNS_OF_FAMILY, (family, profile, STEPPED_BACK_PRICED, PACE_OVER_RUNS))
    busy = [[one.files_total, one.worker_ms, one.seconds or 0] for one in recent]
    rows.append(_price_row(family, profile, "", _pace_of_runs(map(run_record, last), since), busy))
    return rows


register_schema_initializer(COMPONENT, VERSION, initialize, baseline=5)

_UPSERT = """
INSERT INTO work_runs
  (id, family, started_at, updated_at, finished_at, stopped, jobs_done, jobs_failed, worker_ms,
   files, stages, machine, profile, settings, version, products, accelerator, requested_by,
   made_for, ended_with)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
  updated_at = excluded.updated_at,
  finished_at = excluded.finished_at,
  stopped = excluded.stopped,
  jobs_done = excluded.jobs_done,
  jobs_failed = excluded.jobs_failed,
  worker_ms = excluded.worker_ms,
  files = excluded.files,
  stages = excluded.stages,
  settings = excluded.settings,
  products = excluded.products,
  accelerator = excluded.accelerator,
  requested_by = excluded.requested_by,
  made_for = excluded.made_for,
  ended_with = excluded.ended_with
"""

#: A run left open by a process that stopped is closed at the moment it was last written.
_CLOSE_INTERRUPTED = """
UPDATE work_runs
   SET finished_at = updated_at, stopped = 1
 WHERE finished_at IS NULL
"""

#: WHAT ONE ITEM OF THIS KIND OF WORK COST, over the last few hundred finished jobs (kept a week):
#: only job rows hold each item's cost, where a run holds one mean. Divided by `units`, as a walk
#: carries thousands of files; in whole seconds, so `Ledger.pace` refuses what it cannot resolve.
_PER_ITEM_SECONDS = """
SELECT (updated_at - started_at) * 1.0 / MAX(units, 1) AS each, MAX(units, 1) AS weight
  FROM jobs
 WHERE type IN (SELECT value FROM json_each(?))
   AND state = 'done'
   AND started_at IS NOT NULL
   AND updated_at >= started_at
   AND started_at >= ?
   AND NOT EXISTS (
     SELECT 1 FROM json_each(?) AS span
      WHERE jobs.started_at < json_extract(span.value, '$[1]')
        AND jobs.updated_at > json_extract(span.value, '$[0]'))
 ORDER BY started_at DESC, id DESC
 LIMIT ?
"""

#: The last few finished runs of a family, for the pace of work whose items cost under a second.
_LAST_RUNS_OF_FAMILY = """
SELECT * FROM work_runs
 WHERE family = ? AND profile = ? AND finished_at IS NOT NULL AND stopped = 0 AND jobs_done > 0
   AND COALESCE(json_extract(settings, '$."seconds stepped back"'), 0)
       <= ? * (finished_at - started_at)
 ORDER BY started_at DESC
 LIMIT ?
"""

#: The newest runs of a family on this machine, for the price of each kind of file. A stopped run
#: is in it: only the jobs it finished are counted, and those cost what they cost.
_RUNS_FOR_KINDS = """
SELECT * FROM work_runs
 WHERE family = ? AND profile = ? AND finished_at IS NOT NULL AND jobs_done > 0
   AND COALESCE(json_extract(settings, '$."seconds stepped back"'), 0)
       <= ? * (finished_at - started_at)
 ORDER BY started_at DESC, id DESC
 LIMIT ?
"""
#: The same, of the runs that did one kind since the settings last moved: other kinds' runs never
#: push a kind's price out.
_RUNS_FOR_KIND = """
SELECT * FROM work_runs
 WHERE family = ? AND profile = ? AND finished_at IS NOT NULL AND jobs_done > 0
   AND COALESCE(json_extract(settings, '$."seconds stepped back"'), 0)
       <= ? * (finished_at - started_at)
   AND started_at >= ?
   AND EXISTS (SELECT 1 FROM json_each(work_runs.files) one
                WHERE one.key = ? AND json_extract(one.value, '$.n') > 0)
 ORDER BY started_at DESC, id DESC
 LIMIT ?
"""

#: Whether the user a run names is still there. See `_finish`.
_USER_STILL_THERE = "SELECT 1 FROM users WHERE id = ?"
_SAY = "INSERT INTO said_times VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
_SAID = "SELECT at, low, high, left, stalled FROM said_times WHERE run_id = ? ORDER BY at"
_SCORED = "UPDATE work_runs SET time_left = ? WHERE id = ?"
#: RUNS ARE KEPT FOR EVER: the first import may be what is compared years later, and a row is a
#: family's whole stretch of work, so a library scanned daily writes a few hundred a year.

#: A Build task times each product it makes under this prefix: `build.pictures`,
#: `build.faces`, and the ledger files those against the run's products. See `stage`.
BUILD_STAGE_PREFIX = "build."

#: How often an open run is written through, in seconds. What a crash costs.
FLUSH_EVERY_SECONDS = 60.0

#: How many minutes of a run's finished work its pace may be read over: two days.
MINUTES_KEPT = 2880

#: How many finished items a family's pace is measured over, and the fewest it needs before the
#: pace is worth quoting.
#:
#: NOT THE SINGLE NEWEST FINISHED RUN OF THE FAMILY. A run is one family's stretch of work, so a
#: single arriving file is a run of ONE file, and that one file's wall clock would become the price
#: of the whole library: an unchanging backlog estimated at anything from hours to weeks,
#: depending on which small run finished last.
#:
#: Twenty items, because that is where the spread of a per-item cost stops being the story of one
#: file. Two hundred sampled, because the cost of a pass moves with the files it is reading: a
#: sample longer than that is history rather than pace.
PACE_OVER_ITEMS = 200

#: A run stepped back for more of its time than this is not priced from: its pace was a share's.
STEPPED_BACK_PRICED = 0.05
#: The key a run's stepped-back seconds are kept under, beside its settings; absent means none.
STEPPED_BACK = "seconds stepped back"
#: How many stepped-back stretches of this process are kept, to leave their jobs out of a price.
STEPPED_SPANS_KEPT = 256

#: How many finished runs of a family stand in when the per-item clock cannot price it. Each one
#: contributes its own mean, so several runs are a spread and one run is a single figure.
PACE_OVER_RUNS = 5

#: How many of the newest runs that did a kind its price is looked for in, per kind. A kind is
#: priced from those, up to `PACE_OVER_ITEMS` of its files; one that none did has no price, and the
#: estimate says so rather than borrowing another kind's.
KIND_OVER_RUNS = 50
