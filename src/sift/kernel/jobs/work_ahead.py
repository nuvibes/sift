# SPDX-License-Identifier: AGPL-3.0-or-later
"""How much work each kind has that has not been made into a job yet: a bar's denominator.

Counted from what Sift records as done; an old answer is served while one count runs behind it."""

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

#: The dashboard re-reads several times a second during an import; a backlog moves slowly.
FRESH_FOR_SECONDS = 5.0

#: An answer is reused for at least ten times what counting it last cost.
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
    """What is still to come per job type, counted rarely and shared by everything that asks."""

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
        #: None while no answer can be handed out (nothing counted, or `observe` found it wrong).
        self._asked_at: float | None = None
        #: What the last whole count cost, in seconds. See `COUNTING_SHARE`.
        self._cost = 0.0
        #: The one running count, and how often the answer was wrong when it began.
        self._counting: asyncio.Task[None] | None = None
        self._counting_from = -1
        self._counting_finished: dict[str, int] = {}
        self._wrong = 0
        self._runs: dict[str, int] = {}
        #: Finished jobs per kind at the count and now, and the kinds busy now (`observe`).
        self._finished_when_counted: dict[str, int] = {}
        self._finished_now: dict[str, int] = {}
        self._busy_now: frozenset[str] = frozenset()

    def register(self, job_type: str, counter: Counter) -> None:
        """Say how to count what is waiting for one kind of work; a second call replaces it."""
        self._counters[job_type] = counter

    def register_split(self, job_type: str, counter: SplitCounter) -> None:
        """Count waiting work with what of it is on its way, in place of `register`."""
        self._splits[job_type] = counter

    def register_total(self, job_type: str, counter: Counter) -> None:
        """Say how to count the files that want this work, done or not: a bar's denominator."""
        # Without one, a finished library's bar is nought over nought and draws empty.
        self._totals[job_type] = counter

    def register_kinds(self, job_type: str, counter: KindCounter) -> None:
        """Say how to split waiting work by media kind, a mix the time estimate prices by."""
        self._kind_counters[job_type] = counter

    async def counted(self) -> Ahead:
        """Waiting, wanted and the media split from one count, so all three share a cycle."""
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
        """Waiting work per media kind, on the same cycle as `waiting`."""
        return (await self.counted()).by_kind

    async def waiting(self) -> dict[str, int]:
        """Files waiting per kind with no job yet; a failed counter is absent, never nought."""
        return (await self.counted()).waiting

    async def wanted(self) -> dict[str, int]:
        """Files wanting each kind of work, done or not, cached with `waiting` as one moment."""
        return (await self.counted()).wanted

    def observe(self, busy: Iterable[str], finished: Mapping[str, int]) -> None:
        """Note what the queue finished and holds; a run ended since the count makes it wrong."""
        # A moved finished count, not a busy-to-idle edge, so a run shorter than two reads counts.
        now_busy = frozenset(busy)

        def moved(since: Mapping[str, int]) -> bool:
            return any(
                kind not in now_busy and since.get(kind) != done for kind, done in finished.items()
            )

        # A run ending after the running count began makes both answers wrong; one ending
        # before leaves that count right, or every read during a count would restart it.
        if self._counting is not None and moved(self._counting_finished):
            self._asked_at = None
            self._wrong += 1
        elif moved(self._finished_when_counted):
            self._asked_at = None
        self._busy_now = now_busy
        self._finished_now = dict(finished)

    async def _ask(self) -> None:
        """Ensure an answer: an old one is served while a count runs; a wrong one is waited for."""
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
            # Shielded: a reader leaving must not cancel the count others wait for.
            await asyncio.shield(task)
            if began >= needed:
                return

    def _begin(self) -> asyncio.Task[None]:
        """Start the one count. Only ever called with none running."""
        # Finished as of the start, so a run ending during the count still shows as moved.
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
        # Found wrong again while it ran: newest, but not to be handed out.
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
        """Each split kind's waiting into `answer`, with what stands and what arrives."""
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
        """This kind's run: a size fixed at its start that only grows, so `done` never falls."""
        # `wanted`, the library's own count, replaces the run's size wherever a kind has one.
        if wanted is not None:
            # Never below what is left, or the bar would run past its end.
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
