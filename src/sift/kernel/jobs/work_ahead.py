# SPDX-License-Identifier: AGPL-3.0-or-later
"""How much work each kind has that has NOT been made into a job yet.

Named "work ahead" rather than a backlog because this codebase already calls something else that:
`LoopBacklogWatch` measures the event loop's ready queue. Two meanings for one word in one
process is how the wrong one gets read.

## Why this exists

A queue's progress bar needs a denominator, and the work already in the queue cannot be it. That
number GROWS while a pass runs: a scan walks a folder and hands out a
read per file, a catch-up pages through the library queueing a batch at a time, so the total
climbs under the reader, the bar goes backwards, and an estimate of how long is left is worthless
for exactly as long as somebody is watching it most closely.

What is missing is what is *coming*: the files that need this work and have no job for it yet.

## Why it does not need to walk anything

**Sift already records what has been done to every file**, so "what is left" is the library minus
that record: an indexed anti-join, no disk access at all. The one thing it cannot count is a file
nobody has read yet; the folder walks say how many they have left (`to_read`), on the same count.

## Why a request never waits for the count

A whole count can take over a second, and counted on the request every read during one would start
another. So an answer that is merely OLD is handed out while ONE count runs behind it; only one
known to be WRONG (a run has ended since, see `observe`) or not there yet is waited for.

## Why the counters are registered rather than written here

Each of them belongs to whoever owns the record it reads. `face_scans` is the recognition feature's
and `semantic_indexed` is Smart Search's, and only they know what "done" means for their own work:
a file described by a superseded model counts as NOT described, which is a fact about that feature's
revision and nothing the queue could know. A feature may not import another feature, so the
composition root wires them in and this holds the result.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from time import monotonic

from sift.kernel.jobs.queue_rows import FilesToRead
from sift.kernel.log import get_logger

log = get_logger(__name__)

Counter = Callable[[], Awaitable[int]]
"""How many files are waiting for one kind of work, and have no job for it yet."""

KindCounter = Callable[[], Awaitable[Mapping[str, int]]]
"""The same, split by the files' media kind (`video`, `image`, `gif`)."""


@dataclass(frozen=True, slots=True)
class Split:
    """What waits for one kind of work, told apart by whether anything is on its way for it."""

    waiting: int
    standing: int = 0
    """Of those, read files no live job is about: they wait for a run of their task."""
    arriving: dict[str, int] = field(default_factory=dict)
    """The read files waiting that a live job is about, by media kind."""


SplitCounter = Callable[[Sequence[str]], Awaitable[Split]]
"""A `Split`, given every file a live job is about."""

#: How long an answer is reused before it is counted again, at the least.
#:
#: The dashboard re-reads whenever the queue moves, which during an import is several times a
#: second, and a count on each of those is a real cost paid to watch a number that moves slowly.
#: Five seconds is far below the rate at which a backlog of tens of thousands changes visibly and
#: far above the rate at which the screen asks.
FRESH_FOR_SECONDS = 5.0

#: The most of the time the counting may take while a screen watches it: an answer is reused for at
#: least ten times what counting it last cost. The count grows with the library and the five
#: seconds above do not, so without this a library ten times larger would be counted without pause
#: for as long as the screen is open. A count of a second and a half is taken every fifteen seconds.
COUNTING_SHARE = 0.1


@dataclass(frozen=True, slots=True)
class Ahead:
    """One count of what is still to come, every part of it taken on the same cycle."""

    waiting: dict[str, int] = field(default_factory=dict)
    """Files waiting for each kind of work that have no job for it yet. See `WorkAhead.waiting`."""
    wanted: dict[str, int] = field(default_factory=dict)
    """Files that want each kind of work, done or not. See `WorkAhead.wanted`."""
    by_kind: dict[str, dict[str, int]] = field(default_factory=dict)
    """What is waiting, split by media kind. See `WorkAhead.waiting_by_kind`."""
    standing: dict[str, int] = field(default_factory=dict)
    """Of what is waiting, what nothing is on its way for. See `Split`."""
    arriving: dict[str, dict[str, int]] = field(default_factory=dict)
    """What is on its way, by media kind. See `Split`."""
    unread: FilesToRead | None = None
    """What the folder walks have still to read, so a file is counted read or unread, never both."""


@dataclass(frozen=True, slots=True)
class Run:
    """How big one kind's current run is, and how much of it is finished."""

    total: int
    done: int
    left: int


class WorkAhead:
    """What is still to come, per kind of job. Counted rarely and shared by everything that asks.

    Registration is by JOB TYPE (`thumbnail`, `face_scan`) because that is the name the queue
    already knows a kind of work by, and matching on anything else would be a second vocabulary for
    one thing.
    """

    def __init__(
        self,
        *,
        fresh_for: float = FRESH_FOR_SECONDS,
        live_files: Callable[[], Awaitable[Sequence[str]]] | None = None,
        to_read: Callable[[], Awaitable[FilesToRead]] | None = None,
    ) -> None:
        self._counters: dict[str, Counter] = {}
        self._splits: dict[str, SplitCounter] = {}
        self._live_files = live_files
        self._to_read = to_read
        self._unread: FilesToRead | None = None
        self._standing: dict[str, int] = {}
        self._arriving: dict[str, dict[str, int]] = {}
        self._totals: dict[str, Counter] = {}
        self._kind_counters: dict[str, KindCounter] = {}
        self._by_kind: dict[str, dict[str, int]] = {}
        self._fresh_for = fresh_for
        self._answer: dict[str, int] = {}
        self._wanted: dict[str, int] = {}
        #: When the answer held was counted, or None while there is no answer that can be handed
        #: out: nothing has been counted, or a run has ended since (`observe`).
        self._asked_at: float | None = None
        #: What the last whole count cost, in seconds. See `COUNTING_SHARE`.
        self._cost = 0.0
        #: The one count running, if any, and how many times the answer had been found wrong when it
        #: began: a reader waiting for a right answer waits for a count begun after it went wrong.
        self._counting: asyncio.Task[None] | None = None
        self._counting_from = -1
        self._counting_finished: dict[str, int] = {}
        self._wrong = 0
        self._runs: dict[str, int] = {}
        #: How many jobs of each kind the queue had FINISHED when the answer was counted, and
        #: now, with which kinds still have work outstanding now. See `observe`.
        self._finished_when_counted: dict[str, int] = {}
        self._finished_now: dict[str, int] = {}
        self._busy_now: frozenset[str] = frozenset()

    def register(self, job_type: str, counter: Counter) -> None:
        """Say how to count what is waiting for one kind of work.

        Registering the same kind twice replaces the first, which is what makes this safe to call
        from a composition root that may build a feature more than once in a test.
        """
        self._counters[job_type] = counter

    def register_split(self, job_type: str, counter: SplitCounter) -> None:
        """Count what waits for one kind of work with what of it is on its way, in place of
        `register`: while only arriving files run, the rest is not coming."""
        self._splits[job_type] = counter

    def register_total(self, job_type: str, counter: Counter) -> None:
        """Say how to count the files that WANT this kind of work, whether they have it or not.

        The denominator, and it is a different question from the one above: what is left falls to
        nought on a finished library, and without something to divide it by there is nothing to
        say how far through a pass is once it has stopped.

        WITHOUT ONE, THE BAR SITS AT 0 PERCENT WHENEVER NOTHING IS OUTSTANDING, which is every
        moment somebody reads it on a library that has nothing to do. Taken from the size of the
        RUN, a kind that is not busy has no run, so the fraction is nought over nought and the bar
        draws empty over a library that is entirely finished.

        Optional, like the counter: a kind nothing can count a total for falls back to the run's
        own size, which is the best a folder walk can do: there is no record of a file nobody has
        seen yet, so nothing knows how many are coming.
        """
        self._totals[job_type] = counter

    def register_kinds(self, job_type: str, counter: KindCounter) -> None:
        """Say how to split what is waiting for one kind of work by media kind.

        What the estimate of time left prices by, since a video costs a hundred photographs. The
        split is a MIX, not a second count: the estimate reads only its shares, so a counter may
        leave out what it cannot place by kind (files not read yet) without skewing the figure.
        """
        self._kind_counters[job_type] = counter

    async def counted(self) -> Ahead:
        """What is waiting, what is wanted and the split by media kind, from ONE count.

        What a screen drawing all three reads, rather than asking three times: an ask can wait for
        a count, and a run ending in that wait would put the three parts on different cycles.
        """
        await self._ask()
        return Ahead(
            waiting=dict(self._answer),
            wanted=dict(self._wanted),
            by_kind={job_type: dict(kinds) for job_type, kinds in self._by_kind.items()},
            standing=dict(self._standing),
            arriving={job_type: dict(kinds) for job_type, kinds in self._arriving.items()},
            unread=self._unread,
        )

    async def unread_now(self, counted: Ahead) -> FilesToRead | None:
        """What the walks had still to read at `counted`, and whether one waits uncounted now."""
        if self._to_read is None or counted.unread is None:
            return None
        now = await self._to_read()
        return FilesToRead(by_kind=dict(counted.unread.by_kind), uncounted=now.uncounted)

    async def waiting_by_kind(self) -> dict[str, dict[str, int]]:
        """What is waiting for each kind of work, by media kind, where a counter can say.

        Asked on the same cycle as `waiting`; a failed counter is left out, as there.
        """
        return (await self.counted()).by_kind

    async def waiting(self) -> dict[str, int]:
        """How many files are waiting for each kind, and have no job for it yet.

        **A counter that fails is left out rather than reported as nought.** Zero is a real answer
        meaning "nothing left to do", and a feature that is switched off, mid-migration or simply
        broken would otherwise say the library is finished, which is the one wrong answer that
        looks like good news. Absent means "not known", and a bar with no number for a kind draws
        what it does know.
        """
        return (await self.counted()).waiting

    async def wanted(self) -> dict[str, int]:
        """How many files want each kind of work, done or not. The denominator of a bar.

        Asked on the same cycle as `waiting` and cached with it, so the two answers describe one
        moment: a total read a second apart from its own remainder can be smaller than it, which
        draws a bar past its end.
        """
        return (await self.counted()).wanted

    def observe(self, busy: Iterable[str], finished: Mapping[str, int]) -> None:
        """Say what the queue has done and what it still holds, before reading the answer.

        **A RUN THAT HAS ENDED SINCE THE COUNT MAKES THE COUNT WRONG, however young it is.** The
        answer is kept for `FRESH_FOR_SECONDS` because a backlog moves slowly: true while work is
        running and false at the one moment somebody is watching most closely, the end of a run:
        the queue empties at once and the count still holds the files the run has just finished.
        Activity would then draw a finished Generate as "Not started, under a minute" for up to
        five seconds, work outstanding nought and files still waiting, after every Run now.

        `finished` is each kind's count of jobs no longer going to be worked on (done, failed
        or cancelled) over the whole table, which the queue's summary already tallies. A kind
        whose finished count has moved since the answer was counted, and which has nothing
        outstanding now, has ended a run since: the next read counts again, and waits for it. That
        is one recount per run that ends, not one per read, which is what the cache exists to
        prevent; a kind still busy keeps the cached answer however much has finished, because its
        count is about to move again anyway.

        Watching only whether a kind was busy at the count and idle now would miss every run
        shorter than the gap between two reads: three clips generated in under three seconds
        begin and end between one read and the next, nothing is ever seen busy, and the stale
        answer stands for its whole five seconds. A finished count that has moved is a fact about
        the run whether or not anybody was looking while it ran.

        Every kind the queue names counts, a carrier included: a per-file Generate job makes a
        preview, a strip and a fingerprint, and its ending changes three counts at once.
        """
        now_busy = frozenset(busy)

        def moved(since: Mapping[str, int]) -> bool:
            return any(
                kind not in now_busy and since.get(kind) != done for kind, done in finished.items()
            )

        # Two answers can be wrong: the one held, and the one being counted. A run that ended
        # after the running count began makes both wrong, and the next read waits for a count begun
        # after it. One that ended before it began leaves that count right, so the read waits for
        # it rather than starting another: otherwise every read arriving during a count would make
        # it wrong, and a screen asking once a second would never be handed an answer.
        if self._counting is not None and moved(self._counting_finished):
            self._asked_at = None
            self._wrong += 1
        elif moved(self._finished_when_counted):
            self._asked_at = None
        self._busy_now = now_busy
        self._finished_now = dict(finished)

    async def _ask(self) -> None:
        """Make sure there is an answer to hand out. See "Why a request never waits for the count".

        An answer that has only grown old is handed out as it stands and one count starts behind
        it. No answer, or one a run's ending has made wrong, is waited for.
        """
        if self._asked_at is not None:
            reuse_for = max(self._fresh_for, self._cost / COUNTING_SHARE)
            if monotonic() - self._asked_at >= reuse_for and self._counting is None:
                self._begin()
            return
        needed = self._wrong
        while True:
            task, began = self._counting, self._counting_from
            if task is None:
                task, began = self._begin(), self._wrong
            # Shielded: a reader that goes away must not cancel the count every other reader is
            # waiting for.
            await asyncio.shield(task)
            if began >= needed:
                return

    def _begin(self) -> asyncio.Task[None]:
        """Start the one count. Only ever called with none running."""
        # What the queue had finished when the count began, not when it ended: a run ending during
        # the count then still differs from it, and `observe` sends the next read to count again.
        self._counting_finished = dict(self._finished_now)
        self._counting_from = self._wrong
        task = asyncio.get_running_loop().create_task(
            self._recount(self._wrong, self._counting_finished), name="work_ahead.count"
        )
        self._counting = task
        return task

    async def _recount(self, wrong: int, finished: dict[str, int]) -> None:
        started = monotonic()
        try:
            answer = await self._count(self._counters)
            standing, arriving = await self._apart(answer)
            wanted = await self._count(self._totals)
            unread = await self._walks()
            by_kind = await self._split(self._kind_counters)
        finally:
            self._counting = None
        self._answer, self._wanted, self._by_kind = answer, wanted, by_kind
        self._unread = unread
        self._standing, self._arriving = standing, arriving
        self._finished_when_counted = finished
        self._cost = monotonic() - started
        # Found wrong again while it ran: the answer is the newest there is, and still not one to
        # hand out without counting again.
        if self._wrong == wrong:
            self._asked_at = started

    async def _count(self, counters: Mapping[str, Counter]) -> dict[str, int]:
        counted: dict[str, int] = {}
        for job_type, counter in counters.items():
            try:
                counted[job_type] = max(0, await counter())
            # One broken counter must not blind the rest (see `waiting`).
            except Exception as exc:
                log.warning("work_ahead.counter_failed", job_type=job_type, error=str(exc))
        return counted

    async def _apart(
        self, answer: dict[str, int]
    ) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
        """Each split kind's waiting into `answer`, and what of it stands and what is arriving;
        neither where the live files could not be read."""
        standing: dict[str, int] = {}
        arriving: dict[str, dict[str, int]] = {}
        live: list[str] | None = []
        try:
            if self._splits and self._live_files is not None:
                live = list(await self._live_files())
        except Exception as exc:
            log.warning("work_ahead.live_files_failed", error=str(exc))
            live = None
        for job_type, counter in self._splits.items():
            try:
                split = await counter(live or [])
            except Exception as exc:
                log.warning("work_ahead.counter_failed", job_type=job_type, error=str(exc))
                continue
            answer[job_type] = max(0, split.waiting)
            if live is not None:
                standing[job_type] = max(0, min(split.standing, split.waiting))
                arriving[job_type] = {kind: n for kind, n in split.arriving.items() if n > 0}
        return standing, arriving

    async def _walks(self) -> FilesToRead | None:
        try:
            return None if self._to_read is None else await self._to_read()
        except Exception as exc:
            log.warning("work_ahead.walks_failed", error=str(exc))
            return None

    async def _split(self, counters: Mapping[str, KindCounter]) -> dict[str, dict[str, int]]:
        split: dict[str, dict[str, int]] = {}
        for job_type, counter in counters.items():
            try:
                split[job_type] = {kind: n for kind, n in (await counter()).items() if n > 0}
            # One broken counter must not blind the rest (see `waiting`).
            except Exception as exc:
                log.warning("work_ahead.counter_failed", job_type=job_type, error=str(exc))
        return split

    def run_of(
        self, job_type: str, *, left: int, done_already: int, busy: bool, wanted: int | None = None
    ) -> Run:
        """This kind's run: how big it is, how much is finished, how much is left.

        NOT A COUNT OF FINISHED JOBS IN A MOVING WINDOW, which goes BACKWARDS. A window that begins
        at the oldest unfinished job moves FORWARD as the oldest jobs finish, so finished work keeps
        falling out of the far end and the numerator shrinks while the machine is working: the
        "x of total" keeps resetting.

        Nothing here reads a job's timestamps. `left` is counted from the LIBRARY (files that
        still need this work), so it only ever falls as work lands, and `done` is the run's size
        minus it. A number derived from a falling number by subtraction cannot go backwards.

        **The size is fixed when the run begins**, which is what makes it a denominator rather than
        a second moving number. It is taken once, from what was left at that moment plus whatever
        the queue says has already been done, so a screen opened halfway through a run shows the
        run, not the part of it somebody happened to watch.

        It may only grow. Files arriving mid-run raise `left`, and a total below it would report
        more done than there ever was; raising it says a longer run instead, which is what is
        happening.

        A kind that is not busy has no run. The size is forgotten, so the next one is measured
        fresh rather than against a batch that finished hours ago.

        **`wanted` REPLACES ALL OF THAT WHERE A KIND HAS ONE**, and it is the better denominator:
        it is the LIBRARY'S count of the files that want this work, which is defined whether
        anything is running or not. A run's size is only defined while there is a run, so a
        finished library would divide nought by nought and draw an empty bar over work that was
        entirely done, which is the state somebody reading this screen is most often in.
        """
        if wanted is not None:
            # Never below what is left: a total read a moment before a folder arrived would
            # otherwise report more done than there ever was, and the bar would run past its end.
            total = max(wanted, left)
            if not busy:
                self._runs.pop(job_type, None)
            return Run(total=total, done=total - left, left=left)
        if not busy:
            self._runs.pop(job_type, None)
            return Run(total=left, done=0, left=left)

        total = max(self._runs.get(job_type, done_already + left), left)
        self._runs[job_type] = total
        return Run(total=total, done=total - left, left=left)
