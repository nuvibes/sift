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
import time
from collections import Counter, deque
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field

from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.db import Database, Row
from sift.kernel.ids import new_id
from sift.kernel.jobs import time_left
from sift.kernel.jobs.failure_words import KINDS, OTHERWISE, in_plain_words, kind_of
from sift.kernel.jobs.families import FAMILY_LABELS, LONG_PASSES, Family
from sift.kernel.jobs.ledger_store import (
    _CLOSE_INTERRUPTED,
    _FORGET_PRICES,
    _KEEP_PRICE,
    _LAST_RUNS_OF_FAMILY,
    _PER_ITEM_SECONDS,
    _PRICES_OF,
    _RUNS_FOR_KIND,
    _RUNS_FOR_KINDS,
    _SAID,
    _SAY,
    _SCORED,
    _UPSERT,
    _USER_STILL_THERE,
    FLUSH_EVERY_SECONDS,
    KIND_OVER_RUNS,
    MINUTES_KEPT,
    PACE_OVER_RUNS,
    STEPPED_BACK,
    STEPPED_BACK_PRICED,
    STEPPED_SPANS_KEPT,
    _prices_from_runs,
)
from sift.kernel.jobs.ledger_store import (
    BUILD_STAGE_PREFIX as BUILD_STAGE_PREFIX,
)
from sift.kernel.jobs.ledger_store import (
    PACE_OVER_ITEMS as PACE_OVER_ITEMS,
)
from sift.kernel.jobs.pacing import FEWEST_ITEMS as FEWEST_ITEMS
from sift.kernel.jobs.pacing import STRETCH_ITEMS as STRETCH_ITEMS
from sift.kernel.jobs.pacing import Estimate as Estimate
from sift.kernel.jobs.pacing import KindPrices as KindPrices
from sift.kernel.jobs.pacing import Pace as Pace
from sift.kernel.jobs.pacing import (
    _kept_kind_prices,
    _kept_pace,
    _mean,
    _pace_of_runs,
    _price_row,
    _quantile,
    _shares,
    _stretches,
)
from sift.kernel.jobs.pacing import priced as priced
from sift.kernel.jobs.run_records import LAST_RUN_FOR_PRODUCTS as LAST_RUN_FOR_PRODUCTS
from sift.kernel.jobs.run_records import RunReads, run_record
from sift.kernel.jobs.run_records import RunRecord as RunRecord
from sift.kernel.jobs.schedules import get_schedule
from sift.kernel.jobs.time_left import (
    ANY_KIND,
    FEWEST_PRICED,
    PRICED_OVER,
    Key,
    Rate,
    Said,
    Throughput,
    score,
)
from sift.kernel.ledger import Actor, record_event
from sift.kernel.log import get_logger
from sift.kernel.vocabulary import Subject
from sift.kernel.when import stamp as machine_stamp

log = get_logger(__name__)


#: Stages that are not the work: every database statement is timed, and every request.
_NOT_A_STAGE = ("db.", "http.", "job")

#: A long pass's run is its batch: it ends only after this long with nothing due, and a pass after
#: the read not while the read has work due.
RUN_GAP_SECONDS = time_left.BATCH_GAP_SECONDS

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
    #: Each minute's finished items by type and kind, and its seconds stepped back, for the pace.
    minutes: deque[tuple[float, Counter[Key], list[float]]] = field(
        default_factory=lambda: deque(maxlen=MINUTES_KEPT)
    )
    #: When its last job finished (`time.monotonic`), and when it last said its time left.
    last_done: float | None = None
    said_at: float | None = None
    last_flushed: float = 0.0
    began: float = field(default_factory=lambda: time.monotonic())
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


class Ledger(RunReads):
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
        # The newest seconds a worker spent on one item, by type and kind and by type over kinds.
        self._priced: dict[Key, deque[float]] = {}
        # When the numbers that decide the pace last moved, so an older pace is not quoted. None
        # (nothing seen to change in this process) leaves the whole kept history eligible.
        self._settings_changed_at: int | None = None
        # Whether the last tick was stepped back, when it was, and this process's stepped stretches
        # as [start, end] in wall seconds, end None while one is open.
        self._stepped = False
        self._ticked: float | None = None
        self._spans: deque[list[int | None]] = deque(maxlen=STEPPED_SPANS_KEPT)
        # Each family's finished items by minute with its seconds of work due, across its runs.
        self._throughput: dict[Family, Throughput] = {}
        # Each idle long pass: when its gap began counting, and when it last had work (wall).
        self._idle_since: dict[Family, tuple[float, int]] = {}
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
        """What a run is called in its History line: the task whose products it was made for (a
        press's, where it served several), else its family, so Music's run is not "Generate"."""
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
        """A job ended, about a file of the kind the handler said, or unknown.

        `arrived`: files new to the library (`JobContext.arrived`). `failed_with`: the error of a
        job not tried again; `noted`, a done job's note naming a failure kind. `units`: files the
        job FINISHED (`JobContext.units_done`), as a walk's files each get a probe of their own.
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
        # A job that finished no file (a walk) is no file's cost: its hours over one file priced a kind.
        counted.ms += duration_ms if units > 0 else 0.0
        run.last_done = time.monotonic()
        if ok and units > 0:
            self._minute(run)[1][(job_type, kind)] += units
            self.throughput(run.family).done(
                time.monotonic(), units, size_bytes or 0, duration_ms / 1000
            )
            for key in ((job_type, kind), (job_type, ANY_KIND)):
                timed = self._priced.setdefault(key, deque(maxlen=PRICED_OVER))
                timed.append(duration_ms / 1000 / units)

    @staticmethod
    def _minute(run: Run) -> tuple[float, Counter[Key], list[float]]:
        """The run's bucket for this minute of its life."""
        index = int((time.monotonic() - run.began) // 60)
        if not run.minutes or run.minutes[-1][0] != index:
            run.minutes.append((index, Counter(), [0.0]))
        return run.minutes[-1]

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
        ticked = self._ticked
        self._count_stepped(now, stepped_back)
        # A settings change invalidates the pace, so the moment is noted here: this is the one
        # place the live settings arrive, on the pool's own timer. Not on the first tick, which is
        # this process learning what the settings are rather than somebody changing them.
        changed = not self._settings or dict(settings) != self._settings
        if self._settings and changed:
            self._settings_changed_at = now
            self._priced.clear()
            for one in self._throughput.values():
                one.clear()
            log.info("ledger.settings_changed", at=now)
        self._settings = dict(settings)
        # This process's first tick and a settings change move every price; a run's close, its own.
        for family in LONG_PASSES if changed else ():
            await self._keep_prices(family)
        busy = {self.family_of(job_type) for job_type, count in unfinished.items() if count > 0}
        mono = time.monotonic()
        for family in busy:
            self._idle_since.pop(family, None)
            if ticked is not None:
                self.throughput(family).busy(mono, mono - ticked)
        for family, run in list(self._open.items()):
            run.settings = dict(settings)
            if family in busy or (not run.stopped and self._batch_goes_on(family, busy, mono, now)):
                if time.monotonic() - run.last_flushed >= FLUSH_EVERY_SECONDS:
                    await self._write(run, now)
                continue
            run.finished_at = self._idle_since.pop(family, (mono, now))[1]
            await self._finish(run, now)
            del self._open[family]
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
                await self._keep_prices(family)

    def _batch_goes_on(self, family: Family, busy: set[Family], mono: float, now: int) -> bool:
        """Whether an idle long pass's run is still its batch: within the gap, or fed by the read."""
        if family not in LONG_PASSES:
            return False
        gap_from, last_work = self._idle_since.setdefault(family, (mono, now))
        if family is not Family.SCAN and Family.SCAN in busy:
            # The gap counts from the read's end; the run still ends when it last had work.
            self._idle_since[family] = (mono, last_work)
            return True
        return mono - gap_from < RUN_GAP_SECONDS

    def _count_stepped(self, now: int, stepped: bool) -> None:
        """Add the stretch since the last tick to each open run, if it was stepped back."""
        ticked, self._ticked = self._ticked, time.monotonic()
        if self._stepped and ticked is not None:
            for run in self._open.values():
                gone = self._ticked - max(ticked, run.began)
                run.stepped_seconds += gone
                self._minute(run)[2][0] += gone
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
            said = [Said(*one) for one in await connection.execute_fetchall(_SAID, (run.id,))]
            if said:
                await connection.execute(_SCORED, (json.dumps(score(said, now)), run.id))
                await connection.execute("DELETE FROM said_times WHERE run_id = ?", (run.id,))
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
        await self._db.execute("DELETE FROM said_times")

    # --- the estimate ---------------------------------------------------------------------

    def open_run(self, family: Family) -> Run | None:
        return self._open.get(family)

    def prices(self) -> dict[Key, float]:
        """Seconds a worker spent on one item, by type and kind, where enough were timed."""
        return {
            key: sum(one) / len(one)
            for key, one in self._priced.items()
            if len(one) >= FEWEST_PRICED
        }

    def throughput(self, family: Family) -> Throughput:
        return self._throughput.setdefault(family, Throughput())

    def rate(
        self, family: Family, of: int = time_left.ITEMS, over: float = time_left.RATE_OVER
    ) -> Rate | None:
        """What the family's work has finished per second it had work due over its newest `over`
        busy seconds, or None."""
        found = self._throughput.get(family)
        return None if found is None else found.rate(time.monotonic(), of, over)

    def life(self, family: Family) -> float | None:
        """Seconds the family's open run has gone, or None with none open."""
        run = self._open.get(family)
        return None if run is None else time.monotonic() - run.began

    def stopped_for(self, family: Family) -> float | None:
        """Seconds since the family's open run last finished a job, or None with none open."""
        run = self._open.get(family)
        if run is None:
            return None
        return time.monotonic() - (run.began if run.last_done is None else run.last_done)

    def realized(self, family: Family, prices: Mapping[Key, float], within: float) -> float | None:
        """Worker seconds of work the family's run finished per unpaused second, over whole
        minutes back from now covering at most `within`."""
        run = self._open.get(family)
        if run is None:
            return None
        gone = time.monotonic() - run.began
        first = max(0, int(-(-(gone - within) // 60)))
        minutes = [one for one in run.minutes if one[0] >= first]
        unpaused = gone - first * 60 - sum(one[2][0] for one in minutes)
        if unpaused < 60:
            return None
        done = sum(
            n * prices.get(key, prices.get((key[0], ANY_KIND), 0.0))
            for one in minutes
            for key, n in one[1].items()
        )
        return done / unpaused if done > 0 else None

    async def said(
        self,
        family: Family,
        quick: int | None,
        slow: int | None,
        window: tuple[int, int] | None,
        left: int,
    ) -> None:
        """Keep what a live run's row said, at most once a minute; no window is a stopped row."""
        run = self._open.get(family)
        now = time.monotonic()
        if run is None or (run.said_at is not None and now - run.said_at < 60):
            return
        run.said_at = now
        low, high = window if window is not None else (None, None)
        await self._db.execute(
            _SAY, (run.id, int(time.time()), quick, slow, low, high, left, int(window is None))
        )

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
        return _pace_of_runs(map(run_record, rows), self._settings_changed_at or 0)

    async def _keep_prices(self, family: Family) -> None:
        """Write this long pass's prices from its runs and its timed items, for `_kept` to read."""
        since = self._settings_changed_at or 0
        rows = await _prices_from_runs(self._db.fetch_all, family.value, self._profile, since)
        types = sorted(one for one, whose in self._families.items() if whose is family)
        sample = await self._item_costs(types) if types else []
        if sum(weight for _cost, weight in sample) >= FEWEST_ITEMS and _quantile(sample, 0.5) >= 1:
            stretches = _stretches(sample, STRETCH_ITEMS)
            items = Pace(
                sum(weight for _cost, weight in sample),
                min(stretches),
                _mean(sample),
                max(stretches),
                from_items=True,
            )
            rows.append(_price_row(family.value, self._profile, "*", items))
        async with self._db.write() as connection:
            await connection.execute(_FORGET_PRICES, (family.value, self._profile))
            await connection.executemany(_KEEP_PRICE, rows)

    async def _kept(self, family: Family) -> dict[str, Row] | None:
        """A long pass's kept prices by kind, or None where none were kept: then the runs are read."""
        if family not in LONG_PASSES:
            return None
        rows = await self._db.fetch_all(_PRICES_OF, (family.value, self._profile))
        return {str(row["kind"]): row for row in rows} or None

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
        return [run_record(row) for row in rows]

    async def recent_runs(self, family: Family) -> list[RunRecord]:
        """The newest finished runs of a family on this machine since the settings last moved,
        stopped ones included, newest first: the sample every price by kind or product reads."""
        rows = await self._db.fetch_all(
            _RUNS_FOR_KINDS, (family.value, self._profile, STEPPED_BACK_PRICED, KIND_OVER_RUNS)
        )
        runs = [run_record(row) for row in rows]
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

        The history's pace, by kind where `kinds` is given; where the history cannot price it (a
        first pass, which no closed run has priced), the open run's own finished items at its
        measured rate; else a floor from the benchmark's `first_prices` for the videos left.
        """
        if left <= 0:
            return None
        workers = max(1, at_once)
        shares = _shares(kinds)
        kept = await self._kept(family)
        if shares:
            history = await self._estimate_by_kind(family, left, shares, workers, kept)
        else:
            found = (
                await self.pace(family, job_types)
                if kept is None
                else _kept_pace(kept.get("*")) or _kept_pace(kept.get(""))
            )
            history = None if found is None or found.slow <= 0 else found.priced(left, workers)
        videos = left * shares.get("video", 0.0) if shares else left
        return (
            history
            or self._live(family, job_types, left, shares, workers)
            or await self._first_price(family, videos, workers)
        )

    def _live(
        self,
        family: Family,
        job_types: Sequence[str],
        left: float,
        shares: Mapping[str, float],
        workers: int,
    ) -> Estimate | None:
        """The open run's own finished items, at its measured rate, or None with too few timed."""
        prices = self.prices()
        each = time_left.each_item(prices, job_types, shares)
        if family not in self._open or each is None:
            return None
        rate = self.realized(family, prices, time_left.PACE_AT_LEAST)
        low, high = time_left.measured(left * each, rate, workers)
        timed = sum(len(self._priced.get((one, ANY_KIND), ())) for one in job_types)
        return Estimate(low, high, items=timed, at_once=workers)

    async def _estimate_by_kind(
        self,
        family: Family,
        left: float,
        shares: Mapping[str, float],
        workers: int,
        stored: Mapping[str, Row] | None = None,
    ) -> Estimate | None:
        """What is left priced kind by kind from the history, or None when a kind has no price.

        The quick end divides by every worker the family can occupy; the slow end by the workers
        its big runs actually kept busy, where that is fewer.
        """
        prices = (
            await self.kind_prices(family, at_once=workers, kinds=shares)
            if stored is None
            else _kept_kind_prices(stored, workers)
        )
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


#: The end given a stretch still open: later than any job.
_OPEN_SPAN = 1 << 62


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


def _time_left_lines(scored: Mapping[str, object]) -> list[str]:
    """How often a run's time left held its real finish, and how long it waited for other work."""
    lines = []
    if scored.get("right") is not None:
        lines.append(f"Its time left was right in {scored['right']}% of minutes.")
    if stalled := int(str(scored.get("stalled") or 0)):
        lines.append(
            f"It waited for other work for {stalled:,} minute{'s' if stalled > 1 else ''}."
        )
    return lines


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
    lines += _time_left_lines(run.time_left or {})
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
