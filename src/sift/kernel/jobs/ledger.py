# SPDX-License-Identifier: AGPL-3.0-or-later
"""What each pass over the library cost, written down so it can be read back and passed on.

A run is one family's work from the moment it has something to do until it has nothing left: not a
job (a scan of a library is thousands of them) and not a press, which the queue cannot see. It
keeps how long it took, the files of each kind and their bytes, each stage's worker time, the
machine and the settings: enough to say how long a library this size takes on a machine like this
one, and for the time left to start from this machine's own history.

The pool says how a job ended, the handler what file it was about, and the timing hook how long
each stage took, filed under the family of the job running it. The counts are written through
every minute and at the end, so a crash costs at most a minute; a run still open at the next start
was interrupted, and says so.
"""

from __future__ import annotations

import json
import math
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field

from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.db import Connection, Database, Row, in_clause, register_schema_initializer
from sift.kernel.ids import new_id
from sift.kernel.jobs.failure_words import KINDS, OTHERWISE, in_plain_words, kind_of
from sift.kernel.jobs.families import FAMILY_LABELS, LONG_PASSES, Family
from sift.kernel.jobs.schedules import get_schedule
from sift.kernel.ledger import Actor, record_event
from sift.kernel.log import get_logger
from sift.kernel.migrations import column_exists
from sift.kernel.vocabulary import Subject
from sift.kernel.when import stamp as machine_stamp

log = get_logger(__name__)

COMPONENT = "ledger"
VERSION = 6

# `files` and `stages` are JSON rather than rows of their own: a run is read whole or not at all,
# nothing ever asks for one stage across runs, and a row per stage per run would be a table an
# order of magnitude larger holding the same information.
#
# `machine` is the hardware report's label, `profile` its digest: see `HardwareReport.profile`
# for why two runs are comparable only within one digest. `settings` is the handful of numbers
# that decided the pace, so a run can be read beside the settings it ran under.
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
  ended_with   TEXT
)
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

_RECENT = "SELECT * FROM work_runs ORDER BY started_at DESC LIMIT ?"

#: Whether the user a run names is still there. See `_finish`.
_USER_STILL_THERE = "SELECT 1 FROM users WHERE id = ?"
_ONE = "SELECT * FROM work_runs WHERE id = ?"
#: Which of these ids are runs on record. See `Ledger.recorded_among`.
_RECORDED_AMONG = "SELECT id FROM work_runs WHERE id IN (?*)"
#: The last run of a family that ENDED, a stopped one included, on whichever machine: what a long
#: pass's row on Tasks says ran last. A stopped run is the last run, and it was canceled; the pace
#: reads `_LAST_OF_FAMILY` below, which leaves it out because its pace is not the machine's.
_LAST_ENDED_OF_FAMILY = """
SELECT * FROM work_runs
 WHERE family = ? AND finished_at IS NOT NULL
 ORDER BY finished_at DESC, id DESC
 LIMIT 1
"""
_LAST_OF_FAMILY = """
SELECT * FROM work_runs
 WHERE family = ? AND profile = ? AND finished_at IS NOT NULL AND stopped = 0 AND jobs_done > 0
   AND COALESCE(json_extract(settings, '$."seconds stepped back"'), 0)
       <= ? * (finished_at - started_at)
 ORDER BY started_at DESC
 LIMIT 1
"""
#: RUNS ARE KEPT FOR EVER. What somebody wants to compare may be the first import, years later, and
#: a row here is one family's whole stretch of work, not one job, so a library that is scanned every
#: day writes a few hundred rows a year. A record that quietly forgets is worse than a missing
#: record, because nobody is told.

#: THE LAST FINISHED RUN FOR ANY OF THESE PRODUCTS: the one answer to "when did this task last run"
#: for a task whose work is products, read by its row on Tasks and by the Faces screen alike.
#:
#: Bound: the products as one JSON list, then the family whose runs from before `made_for` was kept
#: stand in: those were not migrated, and by family is all they can say. A stopped run counts (it
#: is the last run, and it was canceled); no machine filter, because the library was worked through
#: wherever that happened. Newest by when it ended, the id breaking a tie in one second.
LAST_RUN_FOR_PRODUCTS = """
SELECT * FROM work_runs
 WHERE finished_at IS NOT NULL
   AND (EXISTS (SELECT 1 FROM json_each(work_runs.made_for) made
                 WHERE made.value IN (SELECT value FROM json_each(?)))
        OR (made_for IS NULL AND family = ?))
 ORDER BY finished_at DESC, id DESC
 LIMIT 1
"""

#: A Build task times each product it makes under this prefix: `build.pictures`,
#: `build.faces`, and the ledger files those against the run's products. See `stage`.
BUILD_STAGE_PREFIX = "build."

#: How often an open run is written through, in seconds. What a crash costs.
FLUSH_EVERY_SECONDS = 60.0

#: The recent window the rate of a run is read over, and the long one. The estimate blends them
#: (see `Ledger.rate_per_minute`), so a machine that has just sped up is followed without a single
#: quiet minute reading as the whole pass having stalled.
RECENT_WINDOW_SECONDS = 120.0
LONG_WINDOW_SECONDS = 600.0

#: How long a run has to have been going before its own rate is trusted over the last run's. Below
#: this the window holds a handful of completions and the rate swings with each.
SETTLED_AFTER_SECONDS = 120.0

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
FEWEST_ITEMS = 20

#: The fewest finished items a run under way needs before its OWN pace is quoted, once it has gone
#: for `RATE_AFTER_SECONDS`. Fewer than the history's `FEWEST_ITEMS` on purpose: a task that
#: finishes a file a minute would otherwise run twenty minutes, often most of itself, saying "Not
#: enough to say yet". The run measures its own files on this machine under today's settings, which
#: no history does, so five of them are worth a window; the window is widened for the small sample
#: (`_live_margin`) and narrows toward `LIVE_MARGIN` as the sample reaches `FEWEST_ITEMS`. The
#: history keeps its twenty: it prices files this run has not read yet.
FEWEST_LIVE_ITEMS = 5

#: How many finished runs of a family stand in when the per-item clock cannot price it. Each one
#: contributes its own mean, so several runs are a spread and one run is a single figure.
PACE_OVER_RUNS = 5

#: How many of the newest runs that did a kind its price is looked for in, per kind. A kind is
#: priced from those, up to `PACE_OVER_ITEMS` of its files; one that none did has no price, and the
#: estimate says so rather than borrowing another kind's.
KIND_OVER_RUNS = 50

#: How many items one stretch of the sample holds. The range is quoted between the cheapest and
#: the dearest stretch's mean: what the rest will cost is a SUM of many items, which lands near
#: their mean, and the mean moves with what the files are: a stretch of photographs, a stretch of
#: long videos. Quartiles of single items are the wrong spread for a sum: a pass whose items are
#: mostly cheap and sometimes very dear costs more than its third quarter says, every time.
STRETCH_ITEMS = 10

#: Once a run has a pace of its own, how far either side of it the range is widened. The run's
#: windows agree closely on a steady pass, and a figure quoted to within a few percent is a
#: precision that the files still ahead do not support.
LIVE_MARGIN = 0.15

#: How long a run has to have been going before it has a rate of its own AT ALL: less than a minute
#: is a handful of arrivals, not a pace, and the last run of the family stands in until then.
RATE_AFTER_SECONDS = 60.0

#: Stages that are not the work: every database statement is timed, and every request.
_NOT_A_STAGE = ("db.", "http.", "job")

#: The family of the job the current task is running, for the timing hook's stages. Set by the
#: worker pool around the handler and read by `Ledger.stage`. None outside a job.
CURRENT_FAMILY: ContextVar[Family | None] = ContextVar("sift_job_family", default=None)


@dataclass
class FileCount:
    n: int = 0
    bytes: int = 0
    ms: float = 0.0


@dataclass
class StageCount:
    n: int = 0
    ms: float = 0.0


@dataclass
class Run:
    """One family's run, as it accumulates."""

    id: str
    family: Family
    started_at: int
    machine: str
    profile: str
    version: str
    settings: dict[str, object] = field(default_factory=dict)
    finished_at: int | None = None
    stopped: bool = False
    jobs_done: int = 0
    jobs_failed: int = 0
    worker_ms: float = 0.0
    files: dict[str, FileCount] = field(default_factory=dict)
    stages: dict[str, StageCount] = field(default_factory=dict)
    #: What a Build made, per product: files that got it, and the worker time. See `built`.
    products: dict[str, StageCount] = field(default_factory=dict)
    #: What the graphics card was doing when this was last written: `on`, `off`, `latched_off`, or
    #: empty from a process that had nothing to ask. Re-read on every flush rather than stamped at
    #: the start, because the one value worth having is the one that CHANGES mid-run: a card given
    #: up on halfway through a pass is the explanation for the second half being slower.
    accelerator: str = ""
    #: The user whose press this run is carrying out, or None while nobody's is. See `started`.
    requested_by: str | None = None
    #: Seconds of this run spent with the work stepped back, for either cause.
    stepped_seconds: float = 0.0
    #: Which products this run's jobs were for. See `started` and `_ADD_MADE_FOR`.
    made_for: set[str] = field(default_factory=set)
    #: Why its jobs failed, in plain words, with how many of each.
    ended_with: dict[str, int] = field(default_factory=dict)
    #: Files its jobs brought into the library for the first time (`JobContext.arrived`). Said on
    #: a scan's line in History and not kept on the row: the line is where it is read.
    arrived: int = 0
    #: Which of them a PRESS asked for: the products of the jobs that carried somebody's press.
    #: What names a run made for several tasks' products after the one that was pressed. Not kept
    #: on the row: it is read once, when the run's line is written. See `Ledger.run_name`.
    pressed_for: set[str] = field(default_factory=set)
    #: When each job finished, for the rate. Bounded by the long window rather than by count.
    completions: deque[tuple[float, int]] = field(default_factory=deque)
    """When each job finished and how many files it was about, within the long window."""
    last_flushed: float = 0.0
    began: float = field(default_factory=time.monotonic)
    """When this run started, on the clock that only goes forwards.

    `started_at` is the wall clock and is what the row records; this is what the rate divides by.
    They are not interchangeable: the wall clock steps backwards whenever the machine resynchronises
    its time, and a window that can come out negative would make a pace of infinity."""

    @property
    def seconds(self) -> int | None:
        if self.finished_at is None:
            return None
        return max(0, self.finished_at - self.started_at)

    @property
    def files_total(self) -> int:
        return sum(one.n for one in self.files.values())

    @property
    def bytes_total(self) -> int:
        return sum(one.bytes for one in self.files.values())

    def as_row(self, now: int) -> tuple[object, ...]:
        return (
            self.id,
            self.family.value,
            self.started_at,
            now,
            self.finished_at,
            1 if self.stopped else 0,
            self.jobs_done,
            self.jobs_failed,
            int(self.worker_ms),
            json.dumps(
                {k: {"n": v.n, "bytes": v.bytes, "ms": int(v.ms)} for k, v in self.files.items()}
            ),
            json.dumps({k: {"n": v.n, "ms": int(v.ms)} for k, v in self.stages.items()}),
            self.machine,
            self.profile,
            json.dumps(
                {**self.settings, STEPPED_BACK: int(self.stepped_seconds)}
                if self.stepped_seconds >= 1
                else self.settings
            ),
            self.version,
            json.dumps({k: {"n": v.n, "ms": int(v.ms)} for k, v in self.products.items()}),
            self.accelerator,
            self.requested_by,
            json.dumps(sorted(self.made_for)),
            json.dumps(self.ended_with) if self.ended_with else None,
        )


@dataclass(frozen=True, slots=True)
class RunRecord:
    """A run as read back from the table."""

    id: str
    family: str
    started_at: int
    finished_at: int | None
    stopped: bool
    jobs_done: int
    jobs_failed: int
    worker_ms: int
    files: dict[str, dict[str, int]]
    stages: dict[str, dict[str, int]]
    machine: str
    profile: str
    settings: dict[str, object]
    version: str
    products: dict[str, dict[str, int]] = field(default_factory=dict)
    """What a Build run made, per product: `n` files and `ms` of worker time. Empty for a run of
    any other family, and for a Build recorded before this was kept."""
    accelerator: str = ""
    """What the graphics card was doing: `on`, `off` or `latched_off`. Empty for a run recorded
    before this was kept, which is not the same claim as `off`."""
    requested_by: str | None = None
    """The user whose press this run carried out, or None: nobody pressed, or it was recorded
    before this was kept."""
    made_for: tuple[str, ...] | None = None
    """The products this run's work was for, or None for a run recorded before this was kept,
    which is not the claim that it was for none."""
    ended_with: dict[str, int] = field(default_factory=dict)
    """Why its jobs failed, in plain words, with how many of each."""

    @property
    def seconds(self) -> int | None:
        if self.finished_at is None:
            return None
        return max(0, self.finished_at - self.started_at)

    @property
    def files_total(self) -> int:
        return sum(int(one.get("n", 0)) for one in self.files.values())

    @property
    def bytes_total(self) -> int:
        return sum(int(one.get("bytes", 0)) for one in self.files.values())

    @property
    def jobs_per_minute(self) -> float | None:
        took = self.seconds
        if took is None or took <= 0 or self.jobs_done <= 0:
            return None
        return self.jobs_done * 60 / took

    @property
    def files_per_minute(self) -> float | None:
        """The pace a person compares: files, whatever the jobs were shaped like."""
        took = self.seconds
        if took is None or took <= 0 or self.files_total <= 0:
            return None
        return self.files_total * 60 / took


@dataclass(frozen=True, slots=True)
class Pace:
    """What one item of a family's work costs on this machine: the mean, and how far it moves."""

    items: int
    """How many items the sample covers. Under `FEWEST_ITEMS` there is no pace at all."""
    quick: float
    """Seconds per item over the cheapest stretch of the sample (the cheapest run, from runs)."""
    middle: float
    """Seconds per item over the whole sample: the mean, weighted by items."""
    slow: float
    """Seconds per item over the dearest stretch of the sample (the dearest run, from runs)."""
    from_items: bool
    """True when every item was timed on its own, False when the sample is runs' own means. A
    sample of runs has a spread only where there is more than one run, so a single run answers
    with three equal figures and the screen draws one number rather than a range."""


@dataclass(frozen=True, slots=True)
class Estimate:
    """How long a family's remaining work will take, between two honest bounds."""

    quick_seconds: int
    slow_seconds: int
    items: int
    """The size of the sample it was priced from, so a screen can say how much it rests on."""
    at_once: int
    """The number of workers this family can occupy, which the estimate divides by. The same
    library on the same machine with this halved takes about twice as long, so it travels with
    the figure rather than being assumed by whoever reads it."""
    floor: bool = False
    """The least it takes: the benchmark's price, which counts only the models and the frames."""


@dataclass(frozen=True, slots=True)
class KindPrices:
    """What one file of each kind costs this family, and how many workers its runs kept busy."""

    paces: dict[str, Pace]
    """Worker seconds per item, by media kind (`video`, `image`, `gif`). A kind is absent when the
    runs looked at hold fewer than `FEWEST_ITEMS` of it."""
    busy: float | None
    """Worker seconds per wall second over the runs big enough to fill the pool: how many workers
    the work really kept going, which is fewer than the pool when one kind of work is capped or a
    file is read by one process. None where no run was that big."""


def priced(sample: Sequence[tuple[float, int]]) -> Pace | None:
    """A pace from `(seconds per item, items)` pairs, newest first, or None under `FEWEST_ITEMS`.

    The spread is the cheapest and the dearest stretch of `STRETCH_ITEMS`, so a run of three files
    is not one end of a range on its own, and never narrower than the mean: the oldest few items,
    too few for a stretch of their own, still move the mean, and a range that leaves out its own
    middle is not a range.
    """
    items = sum(weight for _cost, weight in sample)
    if items < FEWEST_ITEMS:
        return None
    stretches = _stretches(sample, STRETCH_ITEMS)
    middle = _mean(sample)
    return Pace(
        items=items,
        quick=min(*stretches, middle),
        middle=middle,
        slow=max(*stretches, middle),
        from_items=False,
    )


def _shares(kinds: Mapping[str, float] | None) -> dict[str, float]:
    """What is left by kind, as fractions of the whole. Empty when nothing says."""
    wanted = {kind: float(n) for kind, n in (kinds or {}).items() if n > 0}
    total = sum(wanted.values())
    return {kind: n / total for kind, n in wanted.items()} if total > 0 else {}


def _quantile(sample: Sequence[tuple[float, int]], at: float) -> float:
    """One quantile of `(cost, weight)` pairs, the weights counting items.

    Weighted rather than plain, because one row is not one item: a scan's walk carries thousands
    of files at one cost, and counting it as a single observation would let a handful of walks
    outvote every file they found. The pairs need not be sorted; nearest-rank, so the answer is
    always a cost that was actually measured rather than an average of two that were not.
    """
    ordered = sorted(sample)
    total = sum(weight for _cost, weight in ordered)
    wanted = at * total
    seen = 0
    # Every cost but the dearest is asked whether the rank falls on it; the dearest is where the
    # rank lands when none of them holds it, which for `at` up to 1 is always so by then.
    for cost, weight in ordered[:-1]:
        seen += weight
        if seen >= wanted:
            return cost
    return ordered[-1][0]


def _mean(sample: Sequence[tuple[float, int]]) -> float:
    """The mean cost of `(cost, weight)` pairs, the weights counting items."""
    total = sum(weight for _cost, weight in sample)
    return sum(cost * weight for cost, weight in sample) / total if total else 0.0


def _stretches(sample: Sequence[tuple[float, int]], size: int) -> list[float]:
    """The mean cost of each run of `size` items, in the order the sample is in (newest first).

    A last run shorter than `size` is the oldest end of the sample and is left out of the spread;
    a sample too small for one whole run is one stretch.
    """
    means: list[float] = []
    held: list[tuple[float, int]] = []
    count = 0
    for cost, weight in sample:
        held.append((cost, weight))
        count += weight
        if count >= size:
            means.append(_mean(held))
            held, count = [], 0
    return means or [_mean(sample)]


def _record(row: Row) -> RunRecord:
    return RunRecord(
        id=str(row["id"]),
        family=str(row["family"]),
        started_at=int(row["started_at"]),
        finished_at=None if row["finished_at"] is None else int(row["finished_at"]),
        stopped=bool(row["stopped"]),
        jobs_done=int(row["jobs_done"]),
        jobs_failed=int(row["jobs_failed"]),
        worker_ms=int(row["worker_ms"]),
        files=json.loads(str(row["files"])),
        stages=json.loads(str(row["stages"])),
        machine=str(row["machine"]),
        profile=str(row["profile"]),
        settings=json.loads(str(row["settings"])),
        version=str(row["version"]),
        products=json.loads(str(row["products"])),
        accelerator=str(row["accelerator"]),
        requested_by=None if row["requested_by"] is None else str(row["requested_by"]),
        made_for=None if row["made_for"] is None else tuple(json.loads(str(row["made_for"]))),
        ended_with=json.loads(str(row["ended_with"] or "{}")),
    )


class Ledger:
    """The runs of every family: the ones going on now, and the record of the ones that finished.

    `machine`, `profile` and `version` describe this process and do not change while it runs;
    `settings` is read from the caller on each tick, because a person can change them mid-run and
    the row should say what was in force at the end.
    """

    def __init__(
        self,
        database: Database,
        *,
        machine: str = "",
        profile: str = "",
        version: str = "",
        families_of: Mapping[str, Family] | None = None,
        accelerator: Callable[[], str] | None = None,
        products_of: Mapping[str, Iterable[str]] | None = None,
    ) -> None:
        self._db = database
        # Which products each job type makes where its payload does not say (`face_scan` makes
        # `faces`); this module knows only their keys. See `started`.
        self._products_of: dict[str, tuple[str, ...]] = {
            job_type: tuple(keys) for job_type, keys in (products_of or {}).items()
        }
        # Which task each product belongs to (`learn_tasks`), naming a run's line in History;
        # until then every run is named by its family.
        self._task_of: dict[str, str] = {}
        # One word when a row is written, so this module knows nothing of encoders; None writes
        # an empty string, which is not the same claim as "off".
        self._accelerator = accelerator
        self._machine = machine
        self._profile = profile
        self._version = version
        self._families: dict[str, Family] = dict(families_of or {})
        self._open: dict[Family, Run] = {}
        self._settings: dict[str, object] = {}
        self._history: dict[Family, RunRecord | None] = {}
        # When the numbers that decide the pace last moved, so an older pace is not quoted. None
        # (nothing seen to change in this process) leaves the whole kept history eligible.
        self._settings_changed_at: int | None = None
        # Whether the last tick was stepped back, when it was, and this process's stepped stretches
        # as [start, end] in wall seconds, end None while one is open.
        self._stepped = False
        self._ticked: float | None = None
        self._spans: deque[list[int | None]] = deque(maxlen=STEPPED_SPANS_KEPT)
        self.first_prices: Callable[[], Awaitable[Mapping[str, float]]] | None = None

    # --- what the pool tells it ------------------------------------------------------------

    def family_of(self, job_type: str) -> Family:
        return self._families.get(job_type, Family.OTHER)

    def learn_families(self, families: Mapping[str, Family]) -> None:
        """Take the registry's answer, once every handler has registered."""
        self._families = dict(families)

    def learn_tasks(self, products_of_task: Mapping[str, Iterable[str]]) -> None:
        """Which products each task is (the composition root's one declaration, the same one the
        tasks' Run now builds from), so a run's line can name the task it was for."""
        self._task_of = {
            product: task_id for task_id, keys in products_of_task.items() for product in keys
        }

    def run_name(self, run: Run) -> str:
        """What a run is called in its History line: the task it was for, else its family.

        Not always the family: Music's press must not read "You ran Generate in 6 s: 192 files"
        when the run is Generate's and the work Music's. The task whose products the run was made
        for, by the title the task registry gives it. A run made for
        several tasks' products (one Identify run reading faces and watermarks for arriving files)
        is named after the one that was pressed where one was, else by its family; so
        is a run that recorded no product, which includes an older run from before products were
        kept.
        """
        tasks = {self._task_of[one] for one in run.made_for if one in self._task_of}
        if len(tasks) > 1:
            tasks = {self._task_of[one] for one in run.pressed_for if one in self._task_of}
        declared = get_schedule(next(iter(tasks))) if len(tasks) == 1 else None
        return declared.title if declared is not None else FAMILY_LABELS[run.family]

    def started(
        self,
        job_type: str,
        *,
        requested_by: str | None = None,
        products: Iterable[str] | None = None,
    ) -> Run:
        """A job of this type has begun. Opens the family's run if none is going.

        The run takes the FIRST `requested_by` it sees, not only its opening job's: a press can land
        while the family drains for an arriving file, and the run is then that press's. A job
        outside the five long passes runs under `OTHER`, out of every pass's pace.
        """
        family = self.family_of(job_type)
        run = self._open.get(family)
        if run is None:
            run = Run(
                id=new_id(),
                family=family,
                started_at=int(time.time()),
                machine=self._machine,
                profile=self._profile,
                version=self._version,
                settings=dict(self._settings),
            )
            self._open[family] = run
            log.info("ledger.run_started", family=family.value, run_id=run.id)
        if run.requested_by is None and requested_by is not None:
            run.requested_by = requested_by
        # WHAT THE JOB WAS FOR: the products its payload names (a Build task, handed `products` by
        # the pool), else the product this type is the maker of. A Generate run of the music
        # product is Music's run; an Identify run of faces alone is not Watermarks'.
        made = tuple(products) if products is not None else self._products_of.get(job_type, ())
        run.made_for.update(made)
        if requested_by is not None:
            run.pressed_for.update(made)
        return run

    def finished(
        self,
        job_type: str,
        *,
        duration_ms: float,
        ok: bool,
        media_type: str | None = None,
        size_bytes: int | None = None,
        units: int = 1,
        arrived: int = 0,
        failed_with: str | None = None,
        noted: str | None = None,
    ) -> None:
        """A job ended. What kind of file it was about is whatever the handler said, or unknown.

        `arrived` is how many files it brought into the library that were not there before
        (`JobContext.arrived`), added up for the run's line. `failed_with` is the error a job that
        will not be tried again ended with; `noted`, a done job's note, kept when it names a
        failure kind (a folder left unread).

        `units` is how many files the job FINISHED (`JobContext.units_done`), not what it was about:
        a walk's files each get a probe of their own, and counted twice they read the pace fifty
        times over.
        """
        run = self.started(job_type)
        if ok:
            run.jobs_done += 1
        else:
            run.jobs_failed += 1
        run.worker_ms += duration_ms
        run.arrived += max(0, arrived)
        said = in_plain_words(failed_with) if failed_with is not None else None
        if said is None and ok and noted and kind_of(noted) is not None:
            said = noted
        if said is not None:
            run.ended_with[said] = run.ended_with.get(said, 0) + 1
        kind = media_type or "unknown"
        counted = run.files.setdefault(kind, FileCount())
        counted.n += max(0, units)
        counted.bytes += size_bytes or 0
        counted.ms += duration_ms
        now = time.monotonic()
        run.completions.append((now, max(0, units)))
        while run.completions and now - run.completions[0][0] > LONG_WINDOW_SECONDS:
            run.completions.popleft()

    def stage(self, stage: str, milliseconds: float) -> None:
        """A stage of some job's work took this long. The timing hook's sink.

        `OTHER` is not skipped here: it has a run of its own, and a stage is named, so two
        unfamilied job types' stages land under two different keys rather than being added together.
        A family with no run open is still skipped, which is the case a stage timed outside any job
        falls into.
        """
        if stage.startswith(_NOT_A_STAGE):
            return
        family = CURRENT_FAMILY.get()
        if family is None:
            return
        run = self._open.get(family)
        if run is None:
            return
        counted = run.stages.setdefault(stage, StageCount())
        counted.n += 1
        counted.ms += milliseconds
        # A Build task times each product it makes as a stage named for it, and that is the
        # record the Build sheet prices the next run from, filed under the product's own name
        # so a reader of the report does not have to know the stage's spelling.
        if stage.startswith(BUILD_STAGE_PREFIX):
            self.built(stage[len(BUILD_STAGE_PREFIX) :], milliseconds)

    def built(self, product: str, milliseconds: float, *, files: int = 1) -> None:
        """A Build task made one product for a file, and it took this long.

        Filed against the run of the family the calling job belongs to, the way a stage is; what
        the Build sheet reads back as the time per file for that product on this machine.
        """
        family = CURRENT_FAMILY.get()
        if family is None:
            return
        run = self._open.get(family)
        if run is None:
            return
        counted = run.products.setdefault(product, StageCount())
        counted.n += max(0, files)
        counted.ms += milliseconds

    async def last_run_for(self, products: Sequence[str], family: Family) -> RunRecord | None:
        """The last finished run for any of these products, or None. `family` answers for the runs
        recorded before products were kept, which were not migrated. See `LAST_RUN_FOR_PRODUCTS`."""
        row = await self._db.fetch_one(
            LAST_RUN_FOR_PRODUCTS, (json.dumps(list(products)), family.value)
        )
        return None if row is None else _record(row)

    async def last_run(self, family: Family) -> RunRecord | None:
        """The last run of a family that ended, stopped or not, or None. See
        `_LAST_ENDED_OF_FAMILY`."""
        row = await self._db.fetch_one(_LAST_ENDED_OF_FAMILY, (family.value,))
        return None if row is None else _record(row)

    def stopped_by_hand(self, job_types: Iterable[str] | None = None) -> None:
        """Somebody stopped work. The open runs it belonged to end as stopped when they next settle.

        With no types every open run is; with types only their families', so one cancelled pass is
        not a Generate run beside it, and its short run stays out of the next price.
        """
        families = None if job_types is None else {self.family_of(one) for one in job_types}
        for family, run in self._open.items():
            if families is None or family in families:
                run.stopped = True

    # --- the timer ------------------------------------------------------------------------

    async def settle(
        self,
        unfinished: Mapping[str, int],
        *,
        settings: Mapping[str, object],
        stepped_back: bool = False,
    ) -> None:
        """Close the runs whose family has nothing left, and write the rest through.

        Called on the pool's own timer with the queue's per-type unfinished counts, which the pool
        has already read for the machine budget: one read, two readers. `stepped_back` is whether
        the work runs on a share now.
        """
        now = int(time.time())
        self._count_stepped(now, stepped_back)
        # A settings change invalidates the pace, so the moment is noted here: this is the one
        # place the live settings arrive, on the pool's own timer. Not on the first tick, which is
        # this process learning what the settings are rather than somebody changing them.
        if self._settings and dict(settings) != self._settings:
            self._settings_changed_at = now
            log.info("ledger.settings_changed", at=now)
        self._settings = dict(settings)
        busy = {self.family_of(job_type) for job_type, count in unfinished.items() if count > 0}
        for family, run in list(self._open.items()):
            run.settings = dict(settings)
            if family in busy:
                if time.monotonic() - run.last_flushed >= FLUSH_EVERY_SECONDS:
                    await self._write(run, now)
                continue
            run.finished_at = now
            await self._finish(run, now)
            del self._open[family]
            self._history.pop(family, None)
            log.info(
                "ledger.run_finished",
                family=family.value,
                run_id=run.id,
                seconds=run.seconds,
                jobs_done=run.jobs_done,
                jobs_failed=run.jobs_failed,
                files=run.files_total,
                stopped=run.stopped,
            )
            # A pass over the library just ended, so the planner's counts are furthest from the
            # truth; every pass drains through here, and `OTHER` moves no table's counts.
            if family in LONG_PASSES:
                await self._db.refresh_statistics(reason=f"pass:{family.value}")

    def _count_stepped(self, now: int, stepped: bool) -> None:
        """Add the stretch since the last tick to each open run, if it was stepped back."""
        ticked, self._ticked = self._ticked, time.monotonic()
        if self._stepped and ticked is not None:
            for run in self._open.values():
                run.stepped_seconds += self._ticked - max(ticked, run.began)
        if stepped and not self._stepped:
            self._spans.append([now, None])
        elif self._stepped and not stepped and self._spans:
            self._spans[-1][1] = now
        self._stepped = stepped

    async def _finish(self, run: Run, now: int) -> None:
        """Write a run's last row and, for a long pass, the `ran` event, in ONE transaction.

        Only the five long passes get an event (`OTHER` would put a line per transcode in the feed).
        The event names the user whose press the run carried out, or Sift; a presser removed since
        is a foreign key that would fail the transaction on every tick, so it is looked up here and
        a gone one reads as Sift, the row keeping the id. Told on the jobs bell.
        """
        async with telling(self._db, EVERY_ADMIN, About.JOBS) as connection:
            await connection.execute(_UPSERT, self._row(run, now))
            if run.family in LONG_PASSES:
                actor = Actor.sift()
                if run.requested_by is not None:
                    if await connection.execute_fetchall(_USER_STILL_THERE, (run.requested_by,)):
                        actor = Actor.user(run.requested_by)
                    else:
                        log.info("ledger.requester_gone", run_id=run.id, family=run.family.value)
                await record_event(
                    connection,
                    actor=actor,
                    verb="ran",
                    subject=Subject("run", run.id, self.run_name(run)),
                    payload=json.dumps(
                        {
                            "family": run.family.value,
                            "seconds": run.seconds,
                            "stopped": run.stopped,
                            "jobs_done": run.jobs_done,
                            "jobs_failed": run.jobs_failed,
                            "files": run.files_total,
                            "bytes": run.bytes_total,
                            # How many of them were new to the library: a scan's, the one pass
                            # that brings files in. Left off the others, whose line would say
                            # "none new" of work that never brings anything in.
                            **({"new": run.arrived} if run.family is Family.SCAN else {}),
                        }
                    ),
                )
        run.last_flushed = time.monotonic()

    async def _write(self, run: Run, now: int) -> None:
        await self._db.execute(_UPSERT, self._row(run, now))
        run.last_flushed = time.monotonic()

    def _row(self, run: Run, now: int) -> tuple[object, ...]:
        """The run as its row, with the card read now. The one place a row is made, for both the
        write-through and the last write (`_finish`)."""
        # Read HERE rather than when the run started: the card can be given up on partway through a
        # pass, and the answer worth keeping is the one that was true when the row was last written.
        if self._accelerator is not None:
            run.accelerator = self._accelerator()
        return run.as_row(now)

    async def start(self) -> None:
        """Close whatever the last process left open.

        It forgets nothing: runs are kept for ever (see the note on keeping runs).
        """
        await self._db.execute(_CLOSE_INTERRUPTED)

    # --- the estimate ---------------------------------------------------------------------

    def open_run(self, family: Family) -> Run | None:
        return self._open.get(family)

    async def rate_per_minute(self, family: Family) -> float | None:
        """How many files of this family's work are finishing per minute, with memory.

        The last two minutes and the last ten blended, each divided by how long the run has been
        going (never by the age of its oldest completion, which reads a burst as a pace), so an
        early reading errs low. Until the run has settled, the last finished run stands in.
        """
        run = self._open.get(family)
        now = time.monotonic()
        own: float | None = None
        if run is not None and run.completions:
            elapsed = now - run.began
            if elapsed >= RATE_AFTER_SECONDS:
                recent = sum(n for at, n in run.completions if now - at <= RECENT_WINDOW_SECONDS)
                longer = sum(n for _at, n in run.completions)
                recent_rate = recent * 60 / min(RECENT_WINDOW_SECONDS, elapsed)
                long_rate = longer * 60 / min(LONG_WINDOW_SECONDS, elapsed)
                own = (recent_rate + long_rate) / 2
                if elapsed >= SETTLED_AFTER_SECONDS:
                    return own
        remembered = await self._remembered_rate(family)
        if own is not None and remembered is not None:
            return (own + remembered) / 2
        return own if own is not None else remembered

    async def _remembered_rate(self, family: Family) -> float | None:
        if family not in self._history:
            row = await self._db.fetch_one(
                _LAST_OF_FAMILY, (family.value, self._profile, STEPPED_BACK_PRICED)
            )
            self._history[family] = None if row is None else _record(row)
        last = self._history[family]
        return None if last is None else last.files_per_minute

    async def pace(self, family: Family, job_types: Sequence[str]) -> Pace | None:
        """What one item of this family's work costs on this machine, or None when nothing can say.

        From the job rows when their whole-second clock can resolve the median item; otherwise from
        the runs' `worker_ms`, a mean per run, so one run is one figure (`Pace.from_items` False).
        Either sample needs `FEWEST_ITEMS` and stops at the last settings change.
        """
        if job_types:
            sample = await self._item_costs(job_types)
            items = sum(weight for _cost, weight in sample)
            # The median decides whether the whole-second clock can resolve these items at all.
            if items >= FEWEST_ITEMS and _quantile(sample, 0.5) >= 1.0:
                stretches = _stretches(sample, STRETCH_ITEMS)
                return Pace(
                    items=items,
                    quick=min(stretches),
                    middle=_mean(sample),
                    slow=max(stretches),
                    from_items=True,
                )
        return await self._pace_from_runs(family)

    async def _item_costs(self, job_types: Sequence[str]) -> list[tuple[float, int]]:
        """The per-item seconds of the last few hundred finished jobs of these types, with the
        number of items each row covered."""
        spans = [[start, _OPEN_SPAN if end is None else end] for start, end in self._spans]
        rows = await self._db.fetch_all(
            _PER_ITEM_SECONDS,
            (
                json.dumps(sorted(job_types)),
                self._settings_changed_at or 0,
                json.dumps(spans),
                PACE_OVER_ITEMS,
            ),
        )
        return [(float(row["each"]), int(row["weight"])) for row in rows]

    async def _pace_from_runs(self, family: Family) -> Pace | None:
        rows = await self._db.fetch_all(
            _LAST_RUNS_OF_FAMILY,
            (family.value, self._profile, STEPPED_BACK_PRICED, PACE_OVER_RUNS),
        )
        sample: list[tuple[float, int]] = []
        for row in rows:
            run = _record(row)
            if self._settings_changed_at is not None and run.started_at < self._settings_changed_at:
                continue
            files = run.files_total
            if files <= 0 or run.worker_ms <= 0:
                continue
            sample.append((run.worker_ms / 1000 / files, files))
        items = sum(weight for _cost, weight in sample)
        if items < FEWEST_ITEMS:
            return None
        return Pace(
            items=items,
            quick=min(cost for cost, _weight in sample),
            middle=_mean(sample),
            slow=max(cost for cost, _weight in sample),
            from_items=False,
        )

    async def kind_prices(
        self, family: Family, *, at_once: int, kinds: Iterable[str] | None = None
    ) -> KindPrices:
        """What one file of each media kind (`kinds`, else those of the newest runs) costs this
        family: each from the newest runs that did it, up to `PACE_OVER_ITEMS` of its files."""
        busy_ms = 0
        wall_ms = 0
        recent = await self.recent_runs(family)
        for run in recent:
            took = run.seconds
            # A run too small to fill the pool says how many files it had, not how many workers
            # the work can keep busy.
            if took and run.files_total >= 2 * max(1, at_once):
                busy_ms += run.worker_ms
                wall_ms += took * 1000
        paces: dict[str, Pace] = {}
        for kind in sorted(
            set(kinds) if kinds is not None else {k for r in recent for k in r.files}
        ):
            sample: list[tuple[float, int]] = []
            for run in await self._runs_for_kind(family, kind):
                n = int(run.files[kind].get("n", 0))
                if sum(weight for _cost, weight in sample) < PACE_OVER_ITEMS:
                    sample.append((int(run.files[kind].get("ms", 0)) / 1000 / n, n))
            if found := priced(sample):
                paces[kind] = found
        return KindPrices(paces=paces, busy=busy_ms / wall_ms if wall_ms > 0 else None)

    async def _runs_for_kind(self, family: Family, kind: str) -> list[RunRecord]:
        rows = await self._db.fetch_all(
            _RUNS_FOR_KIND,
            (
                family.value,
                self._profile,
                STEPPED_BACK_PRICED,
                self._settings_changed_at or 0,
                kind,
                KIND_OVER_RUNS,
            ),
        )
        return [_record(row) for row in rows]

    async def recent_runs(self, family: Family) -> list[RunRecord]:
        """The newest finished runs of a family on this machine since the settings last moved,
        stopped ones included, newest first: the sample every price by kind or product reads."""
        rows = await self._db.fetch_all(
            _RUNS_FOR_KINDS, (family.value, self._profile, STEPPED_BACK_PRICED, KIND_OVER_RUNS)
        )
        runs = [_record(row) for row in rows]
        changed = self._settings_changed_at
        return [run for run in runs if changed is None or run.started_at >= changed]

    async def estimate(
        self,
        family: Family,
        job_types: Sequence[str],
        *,
        left: float,
        at_once: int,
        kinds: Mapping[str, float] | None = None,
    ) -> Estimate | None:
        """How long the family's remaining work will take, between two bounds, or None.

        The run's own pace (widened by `LIVE_MARGIN`), else the history's, by kind where `kinds`
        is given; with neither, a floor from the benchmark's `first_prices` for the videos left.
        """
        if left <= 0:
            return None
        workers = max(1, at_once)
        shares = _shares(kinds)
        live = self._live_range(family, left)
        if live is not None:
            quick, slow, seen = live
            if shares:
                prices = await self.kind_prices(family, at_once=workers, kinds=shares)
                dearer = _dearer_ahead(self._open[family], shares, prices.paces)
                if dearer is None:
                    return None
                quick, slow = quick * dearer, slow * dearer
            return Estimate(
                quick_seconds=int(quick), slow_seconds=int(slow), items=seen, at_once=workers
            )
        if shares:
            by_kind = await self._estimate_by_kind(family, left, shares, workers)
            return by_kind or await self._first_price(
                family, left * shares.get("video", 0.0), workers
            )
        found = await self.pace(family, job_types)
        if found is None or found.slow <= 0:
            return await self._first_price(family, left, workers)
        quick = left * found.quick / workers
        slow = left * found.slow / workers
        return Estimate(
            quick_seconds=int(quick),
            slow_seconds=int(max(slow, quick)),
            items=found.items,
            at_once=workers,
        )

    async def _estimate_by_kind(
        self, family: Family, left: float, shares: Mapping[str, float], workers: int
    ) -> Estimate | None:
        """What is left priced kind by kind from the history, or None when a kind has no price.

        The quick end divides by every worker the family can occupy; the slow end by the workers
        its big runs actually kept busy, where that is fewer.
        """
        prices = await self.kind_prices(family, at_once=workers, kinds=shares)
        quick = 0.0
        slow = 0.0
        items = 0
        for kind, share in shares.items():
            found = prices.paces.get(kind)
            if found is None:
                return None
            quick += left * share * found.quick
            slow += left * share * found.slow
            items += found.items
        kept = min(float(workers), prices.busy) if prices.busy else float(workers)
        return Estimate(
            quick_seconds=int(quick / workers),
            slow_seconds=int(max(slow / max(kept, 1.0), quick / workers)),
            items=items,
            at_once=workers,
        )

    async def _first_price(self, family: Family, files: float, workers: int) -> Estimate | None:
        each = (await self.first_prices()).get(family.value) if self.first_prices else None
        least = int(files * (each or 0) / workers)
        return Estimate(least, least, 0, workers, floor=True) if least > 0 else None

    def _live_range(self, family: Family, left: float) -> tuple[float, float, int] | None:
        """What is left over the run's own fastest and slowest window, with the items they saw.

        None until the run has gone for `RATE_AFTER_SECONDS` and finished `FEWEST_LIVE_ITEMS`. A
        window with nothing finished in it is a stall or a pause rather than a pace, and is left
        out. Under `FEWEST_ITEMS` the range is widened for the sample it stands on
        (`_live_margin`), so an early window is honest about how little is behind it.
        """
        run = self._open.get(family)
        # Housekeeping's run is every chore at once, so its pace belongs to none of them.
        if run is None or family is Family.OTHER:
            return None
        now = time.monotonic()
        elapsed = now - run.began
        seen = sum(n for _at, n in run.completions)
        if elapsed < RATE_AFTER_SECONDS or seen < FEWEST_LIVE_ITEMS:
            return None
        recent = sum(n for at, n in run.completions if now - at <= RECENT_WINDOW_SECONDS)
        rates = [
            recent / min(RECENT_WINDOW_SECONDS, elapsed),
            seen / min(LONG_WINDOW_SECONDS, elapsed),
            run.files_total / elapsed,
        ]
        # Never empty: `seen` is at least `FEWEST_LIVE_ITEMS` by the floor above, so the whole-run
        # window always moves. A window with nothing finished in it is a stall, and is left out.
        moving = [rate for rate in rates if rate > 0]
        margin = _live_margin(seen)
        return (
            left / max(moving) * max(0.0, 1 - margin),
            left / min(moving) * (1 + margin),
            seen,
        )

    # --- reading back -----------------------------------------------------------------------

    async def recent(self, *, limit: int = 30) -> list[RunRecord]:
        return [_record(row) for row in await self._db.fetch_all(_RECENT, (limit,))]

    async def get(self, run_id: str) -> RunRecord | None:
        row = await self._db.fetch_one(_ONE, (run_id,))
        return None if row is None else _record(row)

    async def recorded_among(self, run_ids: Sequence[str]) -> set[str]:
        """Which of these ids are runs this ledger holds: the runs a report can be made for.

        One statement for a page of History, whose "ran" lines name two kinds of run under one
        subject kind: a pass over the library, which is a row here, and a task's own run, whose
        subject is the TASK (`JobQueue.record_runs_of`) and has no row here to report on.
        """
        wanted = sorted(set(run_ids))
        if not wanted:
            return set()
        asked, values = in_clause(_RECORDED_AMONG, wanted)
        return {str(row["id"]) for row in await self._db.fetch_all(asked, values)}


#: The end given a stretch still open: later than any job.
_OPEN_SPAN = 1 << 62


def _live_margin(seen: int) -> float:
    """How far either side of a run's own pace its range is widened, for the items behind it.

    `LIVE_MARGIN` from `FEWEST_ITEMS` up. Below that the margin grows as one over the square root
    of the sample, the way the error of a mean does: five items give twice the margin twenty do,
    so the first window a slow task shows is wide and closes in as it goes.
    """
    if seen >= FEWEST_ITEMS:
        return LIVE_MARGIN
    return LIVE_MARGIN * math.sqrt(FEWEST_ITEMS / max(seen, 1))


def _dearer_ahead(
    run: Run, shares: Mapping[str, float], history: Mapping[str, Pace]
) -> float | None:
    """How much dearer a file of what is left is than a file of what this run has done.

    The run's pace is files per second of the files it has done. The files ahead cost their kind's
    price (the run's own where it has done `FEWEST_ITEMS` of that kind, else the history's, else
    the run's own from `FEWEST_LIVE_ITEMS`), so the pace is scaled by their mean over the run's own
    mean. None where a kind ahead has no price. The run's own small sample comes last: the history
    has more files behind it, and a kind with no history at all would otherwise leave a run of one
    kind, well under way, with nothing to say about the rest of that same kind.
    """
    done = [one for one in run.files.values() if one.n > 0]
    done_n = sum(one.n for one in done)
    done_ms = sum(one.ms for one in done)
    if done_n <= 0 or done_ms <= 0:
        return None
    ahead = 0.0
    for kind, share in shares.items():
        own = run.files.get(kind)
        if own is not None and own.n >= FEWEST_ITEMS:
            cost = own.ms / own.n
        elif kind in history:
            cost = history[kind].middle * 1000
        elif own is not None and own.n >= FEWEST_LIVE_ITEMS:
            cost = own.ms / own.n
        else:
            return None
        ahead += share * cost
    return ahead / (done_ms / done_n)


# --- the report a person copies ----------------------------------------------------------------


def _took(seconds: int | None) -> str:
    if seconds is None:
        return "still going"
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 5400:
        return f"{seconds / 60:.1f} min"
    return f"{seconds / 3600:.1f} hr"


def _size(count: int) -> str:
    if count >= 1_000_000_000_000:
        return f"{count / 1_000_000_000_000:.2f} TB"
    if count >= 1_000_000_000:
        return f"{count / 1_000_000_000:.1f} GB"
    if count >= 1_000_000:
        return f"{count / 1_000_000:.0f} MB"
    return f"{count} bytes"


_FAILED = {one.words for one in KINDS} | {OTHERWISE}


def _label(family: str) -> str:
    """A family retired from the enum still reports, under the name its row has."""
    try:
        return FAMILY_LABELS[Family(family)]
    except ValueError:
        return family


def report_text(run: RunRecord) -> str:
    """A run as plain text to paste: every line a fact the table holds, in ASCII."""
    when = machine_stamp(run.started_at, "%Y-%m-%d %H:%M")
    lines = [
        f"Sift {run.version}, {_label(run.family)} run, {when}, took {_took(run.seconds)}"
        + (" (stopped by hand)" if run.stopped else ""),
        f"Machine: {run.machine or 'unknown'}",
    ]
    if run.settings:
        said = ", ".join(f"{key} {value}" for key, value in sorted(run.settings.items()))
        lines.append(f"Settings: {said}")
    # A kind nobody named with no bytes is a job about no file (a walk): nothing to say of it.
    known = {k: one for k, one in run.files.items() if k != "unknown" or one.get("bytes")}
    kinds = ", ".join(
        f"{int(one.get('n', 0)):,} {kind} ({_size(int(one.get('bytes', 0)))})"
        for kind, one in sorted(known.items())
    )
    if kinds or not run.files:
        lines.append(f"Files: {kinds or 'none recorded'}")
    lines.append(f"Jobs: {run.jobs_done:,} done, {run.jobs_failed:,} failed")
    ended = sorted(run.ended_with.items(), key=lambda one: (-one[1], one[0]))
    lines += [
        f"Why {n:,} failed: {words}"
        if words in _FAILED
        else f"{'Ended' if n == 1 else f'{n:,} ended'} with: {words}"
        for words, n in ended
    ]
    took = run.seconds
    if took and took > 0 and run.jobs_done:
        moved = f", {_size(int(run.bytes_total * 60 / took))}/min" if run.bytes_total else ""
        lines.append(f"Pace: {run.jobs_done * 60 / took:.1f} jobs/min{moved}")
    for kind, one in sorted(known.items()):
        n = int(one.get("n", 0))
        if n:
            lines.append(
                f"Per {kind}: {int(one.get('ms', 0)) / n / 1000:.1f} s of worker time each"
            )
    if run.products:
        lines.append("Built (worker time):")
        for product, one in sorted(
            run.products.items(), key=lambda item: -int(item[1].get("ms", 0))
        ):
            n = int(one.get("n", 0))
            ms = int(one.get("ms", 0))
            each = f", {ms / n / 1000:.1f} s each" if n else ""
            lines.append(f"  {product}: {n:,} files, {ms / 1000:.0f} s{each}")
    if run.stages:
        lines.append("Stages (worker time):")
        for stage, one in sorted(run.stages.items(), key=lambda item: -int(item[1].get("ms", 0))):
            n = int(one.get("n", 0))
            ms = int(one.get("ms", 0))
            each = f", {ms / n / 1000:.2f} s each" if n else ""
            lines.append(f"  {stage}: {ms / 1000:.0f} s over {n:,}{each}")
    return "\n".join(lines)
