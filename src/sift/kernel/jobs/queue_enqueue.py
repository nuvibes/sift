# SPDX-License-Identifier: AGPL-3.0-or-later
"""Putting work in: one job, a batch, a collapse onto the same work waiting, and a settle."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from sift.kernel.db import Connection
from sift.kernel.ids import new_id
from sift.kernel.jobs.queue_core import ASKED_AT, Arrival, QueueCore
from sift.kernel.jobs.queue_rows import UnknownJobType, _check_payload, _fetch
from sift.kernel.jobs.quiet_hours import AT_NOW, ATS
from sift.kernel.jobs.switchboard import JobSwitchedOff
from sift.kernel.jobs.tuning import (
    BACKGROUND_PRIORITY,
    BATCH_SETTLE_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_PRIORITY,
    MAX_ATTEMPTS_CEILING,
    PRIORITY_MAX,
    PRIORITY_MIN,
    SETTLE_LONGEST_SECONDS,
)
from sift.kernel.log import get_logger

log = get_logger("sift.kernel.jobs.queue")
# `root_id` is the parent's family, or the row's own id at the top, decided in the statement from
# the parent row itself so no caller can hand a child the wrong family.
_INSERT = (
    "INSERT INTO jobs (id, parent_id, type, state, priority, payload, max_attempts, "
    "run_after, created_at, updated_at, requested_by, timing, root_id) "
    "VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, COALESCE("
    "(SELECT COALESCE(parent.root_id, parent.id) FROM jobs parent WHERE parent.id = ?), ?))"
)
#: The insert, saying the family it chose, so the queue can tell whether a worker could take it now.
_PLACE = _INSERT + " RETURNING root_id"

#: How much earlier than a minute after the last file a settle may run, so a batch arriving moves
#: its waiting row once in this long rather than once per file. See `enqueue_when_settled`.
SETTLE_SLACK_SECONDS = 10


#: The type of a press's head: a row that runs nothing, its files its steps.
PRESS = "press"
#: A press's head, written done in the transaction that writes its steps: it is never claimed.
_SETTLE_HEAD = "UPDATE jobs SET state = 'done', progress = 0 WHERE id = ?"
#: A head whose every file collapsed onto work already waiting heads nothing.
_FORGET_HEAD = "DELETE FROM jobs WHERE id = ?"


def _under(row: tuple[Any, ...], head: str) -> tuple[Any, ...]:
    """An `_INSERT` row made a step of `head`: its parent, and the parent its family is read from."""
    return (row[0], head, *row[2:11], head, row[12])


# An identical job already WAITING, for `enqueue(dedupe=True)`. Never one running: it may have
# walked past the change that prompted the new request, and collapsing onto it would lose that.
# The live states said too, word for word, so the live rows' index (`ix_jobs_live_payload`) serves it.
_PENDING_LIKE = (
    "SELECT id, priority, requested_by, timing, run_after, created_at FROM jobs"
    " WHERE type = ? AND payload = ? AND state IN ('queued', 'running', 'blocked', 'paused')"
    " AND state = 'queued' LIMIT 1"
)

# A settle collapsing onto the one already waiting: the waiting row is put off to a minute after
# THIS file, never earlier than it was and never past its longest. See `enqueue_when_settled`.
# Only a row that is itself waiting for a moment (`run_after IS NOT NULL`): one that runs
# immediately is a press or a watcher's walk, and a settle arriving must not hold it back.
_PUT_OFF = (
    "UPDATE jobs SET run_after = ?, updated_at = ?"
    " WHERE id = ? AND state = 'queued' AND run_after IS NOT NULL AND run_after < ?"
)

# A press collapsing onto a waiting row makes it run now, its wait cleared with it: a press is never
# held back by a settle's `run_after` or by quiet hours. Only ever TO `now`.
_RUN_NOW = (
    "UPDATE jobs SET timing = 'now', run_after = NULL, updated_at = ?"
    " WHERE id = ? AND state = 'queued'"
    " AND (COALESCE(timing, '') <> 'now' OR run_after IS NOT NULL)"
)

# Making a waiting job more urgent, for a collapse. `state = 'queued'` is repeated although the row
# was just read so, so the statement can never re-prioritise a job already running.
_RAISE_PRIORITY = "UPDATE jobs SET priority = ?, updated_at = ? WHERE id = ? AND state = 'queued'"

# Naming the person on a waiting job nobody had pressed for, for a collapse: the first person to
# ask is the one who asked.
_NAME_REQUESTER = (
    "UPDATE jobs SET requested_by = ?, updated_at = ?"
    " WHERE id = ? AND state = 'queued' AND requested_by IS NULL"
)

# Work nobody pressed that is waiting to run, of one type. What a changed schedule takes back
# before it places the next run, so the old moment cannot run as well as the new one.
_WITHDRAW_WAITING = """
UPDATE jobs
   SET state = 'canceled', updated_at = ?
 WHERE type = ? AND state = 'queued' AND timing IS NULL AND parent_id IS NULL
RETURNING id
"""

# Moving the one waiting run nobody pressed to a new moment. See `JobQueue.retime_waiting`.
_RETIME_WAITING = """
UPDATE jobs
   SET run_after = ?, updated_at = ?
 WHERE id = ? AND state = 'queued' AND timing IS NULL
RETURNING id
"""


# How many files a waiting job is about, counted ahead of it. See `JobQueue.set_waiting_units`.
_SET_WAITING_UNITS = (
    "UPDATE jobs SET units = ?, updated_at = ? WHERE id = ? AND state = 'queued' RETURNING id"
)


async def _place(connection: Connection, row: tuple[Any, ...]) -> str:
    """Insert one `_INSERT` row; the family the statement gave it."""
    rows = await _fetch(connection, _PLACE, row)
    return str(rows[0]["root_id"])


class Enqueuing(QueueCore):
    """Putting work into the queue."""

    async def enqueue(
        self,
        job_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        priority: int = DEFAULT_PRIORITY,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        parent_id: str | None = None,
        run_after: int | None = None,
        require_handler: bool = True,
        dedupe: bool = False,
        requested_by: str | None = None,
        at: str | None = None,
        settling: bool = False,
        with_row: tuple[str, tuple[Any, ...]] | None = None,
    ) -> str:
        """Add a job. Returns its id.

        A lower `priority` runs first; equal priorities run oldest first. `run_after` is a unix time
        before which no worker may take it (None: as soon as one is free), a column rather than a
        timer, so a time already past is simply claimable now and catching up works.

        An unregistered type is refused here, where the mistake is (`require_handler=False` is for
        a handler registered later, and for tests). Work switched off raises `JobSwitchedOff`.

        `dedupe` collapses this onto an identical job already waiting and returns its id: for work
        that names a PLACE, so a watcher told of one folder fifty times queues one scan.

        `requested_by` is the user whose press this is (None for a schedule, a watcher or a
        handler's children). `at` says when: `now` runs whatever quiet hours say, `quiet` waits for
        them; left out, a press is `now` and anything else follows its task's When. `settling` is
        `enqueue_when_settled`'s: its urgency is final, and a collapse puts the waiting row off to
        this request's `run_after`. `with_row` is a caller's own statement written in the same transaction, its first parameter
        the job id: the row joins whichever job the enqueue answers, a collapsed one included.
        """
        priority, timing = await self._admit(
            job_type,
            priority=priority,
            max_attempts=max_attempts,
            requested_by=requested_by,
            at=at,
            parent_id=parent_id,
            require_handler=require_handler,
            held_to_urgency=not settling,
        )
        job_id, row, serialized = self._row(
            job_type,
            payload,
            priority=priority,
            max_attempts=max_attempts,
            parent_id=parent_id,
            run_after=run_after,
            requested_by=requested_by,
            timing=timing,
        )

        if dedupe:
            placed, family = await self._enqueue_collapsing(
                job_type,
                serialized,
                row,
                priority=priority,
                requested_by=requested_by,
                timing=timing,
                settle_at=run_after if settling else None,
                with_row=with_row,
            )
            if family is None:
                return placed
        else:
            # Told too: a job that has only just been queued is a row on the dashboard, and a
            # quiet insert would leave it off the screen until something else in the queue moved.
            async with self._writing() as connection:
                family = await _place(connection, row)
                if with_row is not None:
                    await connection.execute(with_row[0], (job_id, *with_row[1]))

        log.info("job.enqueued", job_id=job_id, job_type=job_type, parent_id=parent_id)
        self._work_arrived([Arrival(job_type, family, timing, run_after)])
        return job_id

    async def enqueue_children(
        self,
        parent_id: str,
        children: Sequence[tuple[str, Mapping[str, Any]]],
        *,
        priority: int = DEFAULT_PRIORITY,
        at: str | None = None,
    ) -> list[str]:
        """Hand out several jobs under a running one in ONE write: the work a file's read starts.

        Each type is admitted as `enqueue` admits it, before anything is written, so a switched-off
        one raises `JobSwitchedOff` and nothing is queued. A child already waiting is collapsed onto
        as `dedupe` does, so a read that runs twice hands its work out once. Returns the ids in the
        order given, which is the order they are claimed in at one urgency.
        """
        placing = []
        for job_type, payload in children:
            urgency, timing = await self._admit(
                job_type,
                priority=priority,
                max_attempts=DEFAULT_MAX_ATTEMPTS,
                requested_by=None,
                at=at,
                parent_id=parent_id,
                require_handler=True,
            )
            job_id, row, serialized = self._row(
                job_type,
                payload,
                priority=urgency,
                max_attempts=DEFAULT_MAX_ATTEMPTS,
                parent_id=parent_id,
                run_after=None,
                requested_by=None,
                timing=timing,
            )
            placing.append((job_type, serialized, row, urgency, timing))
        if not placing:
            return []
        arrivals, placed = [], []
        async with self._writing() as connection:
            for job_type, serialized, row, urgency, timing in placing:
                job_id, family = await self._collapse_or_insert(
                    connection,
                    job_type,
                    serialized,
                    row,
                    priority=urgency,
                    requested_by=None,
                    timing=timing,
                )
                placed.append(job_id)
                if family is not None:
                    arrivals.append(Arrival(job_type, family, timing, None))
                    log.info("job.enqueued", job_id=job_id, job_type=job_type, parent_id=parent_id)
        self._work_arrived(arrivals)
        return placed

    async def enqueue_many(
        self,
        job_type: str,
        payloads: Sequence[Mapping[str, Any]],
        *,
        priority: int = DEFAULT_PRIORITY,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        require_handler: bool = True,
        dedupe: bool = False,
        requested_by: str | None = None,
        at: str | None = None,
        title: str | None = None,
    ) -> list[str]:
        """Add one job of one type per payload, in ONE write transaction (`enqueue`'s checks once
        for the batch, `dedupe` per row). Returns the ids in order. A press (`requested_by`) is one
        family: a head row named `title` with every new file a step under it."""
        if not payloads:
            return []
        priority, timing = await self._admit(
            job_type,
            priority=priority,
            max_attempts=max_attempts,
            requested_by=requested_by,
            at=at,
            parent_id=None,
            require_handler=require_handler,
        )
        rows = [
            self._row(
                job_type,
                payload,
                priority=priority,
                max_attempts=max_attempts,
                parent_id=None,
                run_after=None,
                requested_by=requested_by,
                timing=timing,
            )
            for payload in payloads
        ]
        placed: list[str] = []
        families: list[str] = []
        # `_writing` either way: a collapse moves the dashboard as an insert does.
        async with self._writing() as connection:
            head = None if requested_by is None else await self._press_head(connection, rows, title)
            for job_id, written, serialized in rows:
                row = written if head is None else _under(written, head)
                if dedupe:
                    one, family = await self._collapse_or_insert(
                        connection,
                        job_type,
                        serialized,
                        row,
                        priority=priority,
                        requested_by=requested_by,
                        timing=timing,
                    )
                    placed.append(one)
                else:
                    family = await _place(connection, row)
                    placed.append(job_id)
                if family is not None:
                    families.append(family)
            new = sorted(set(placed) & {one[0] for one in rows})
            if head is not None and not new:
                await connection.execute(_FORGET_HEAD, (head,))
        for job_id in new:
            log.info("job.enqueued", job_id=job_id, job_type=job_type, parent_id=head)
        self._work_arrived(Arrival(job_type, family, timing, None) for family in families)
        return placed

    async def _press_head(
        self,
        connection: Connection,
        rows: Sequence[tuple[str, tuple[Any, ...], str]],
        title: str | None,
    ) -> str:
        """The row a press's files are steps of, settled done so only its steps run; its payload
        carries the press's words."""
        head = new_id()
        first = rows[0][1]
        # The first file's payload, so the press is read as one over some files making its products.
        body = json.dumps({**json.loads(rows[0][2]), **({"title": title} if title else {})})
        await connection.execute(
            _INSERT, (head, None, PRESS, first[3], body, *first[5:11], None, head)
        )
        await connection.execute(_SETTLE_HEAD, (head,))
        return head

    async def _admit(
        self,
        job_type: str,
        *,
        priority: int,
        max_attempts: int,
        requested_by: str | None,
        at: str | None,
        parent_id: str | None,
        require_handler: bool,
        held_to_urgency: bool = True,
    ) -> tuple[int, str | None]:
        """Whether this kind of work may be queued at all, and at what urgency and timing.

        The half of `enqueue` that is about the TYPE rather than the row, written once so that
        `enqueue_many` refuses and raises exactly what `enqueue` does. Returns the priority after
        the type's ceiling and the timing word.
        """
        from sift.kernel.jobs.worker_pool import registered_handlers, registered_urgency

        if at is not None and at not in ATS:
            raise ValueError(f"at must be one of {', '.join(ATS)}")
        timing = at if at is not None else (AT_NOW if requested_by is not None else None)

        # ONE URGENCY PER KIND OF WORK, declared at the handler (`register_handler(urgency=...)`),
        # so a pass asked for two ways cannot run twice for one answer. Only ever downwards.
        ceiling = registered_urgency(job_type)
        if held_to_urgency and ceiling is not None and priority < ceiling:
            log.info("job.urgency_held", job_type=job_type, asked=priority, urgency=ceiling)
            priority = ceiling

        if require_handler and job_type not in registered_handlers():
            raise UnknownJobType(
                f"no handler is registered for job type {job_type!r}. Register one with "
                "register_handler() before enqueuing it."
            )
        # SWITCHED OFF IS REFUSED HERE, where the request is, rather than written, claimed, run and
        # thrown away once per file for as long as the switch stays off.
        refused = await self._switchboard.refusal(job_type, pressed=timing is not None)
        if refused is not None:
            log.info("job.switched_off", job_type=job_type, parent_id=parent_id)
            raise JobSwitchedOff(refused)
        if not 1 <= max_attempts <= MAX_ATTEMPTS_CEILING:
            raise ValueError(f"max_attempts must be between 1 and {MAX_ATTEMPTS_CEILING}")
        if not PRIORITY_MIN <= priority <= PRIORITY_MAX:
            raise ValueError(f"priority must be between {PRIORITY_MIN} and {PRIORITY_MAX}")

        return priority, timing

    def _row(
        self,
        job_type: str,
        payload: Mapping[str, Any] | None,
        *,
        priority: int,
        max_attempts: int,
        parent_id: str | None,
        run_after: int | None,
        requested_by: str | None,
        timing: str | None,
    ) -> tuple[str, tuple[Any, ...], str]:
        """One job's row for `_INSERT`, its payload checked: the new id, the row, the payload text."""
        body = dict(payload or {})
        _check_payload(body)
        serialized = json.dumps(body)

        job_id = new_id()
        now = self._now()
        row = (
            job_id,
            parent_id,
            job_type,
            priority,
            serialized,
            max_attempts,
            run_after,
            now,
            now,
            requested_by,
            timing,
            # The family: the parent's, read by the statement, or this job's own id at the top.
            parent_id,
            job_id,
        )
        return job_id, row, serialized

    async def _enqueue_collapsing(
        self,
        job_type: str,
        serialized: str,
        row: tuple[Any, ...],
        *,
        priority: int,
        requested_by: str | None,
        timing: str | None,
        settle_at: int | None,
        with_row: tuple[str, tuple[Any, ...]] | None,
    ) -> tuple[str, str | None]:
        """`enqueue`'s `dedupe`: the id answered and the family joined, None where it collapsed."""
        # The look and the insert share one write transaction, so two callers noticing the same
        # folder in the same instant cannot both find nothing waiting and both queue.
        async with self._writing() as connection:
            placed, family = await self._collapse_or_insert(
                connection,
                job_type,
                serialized,
                row,
                priority=priority,
                requested_by=requested_by,
                timing=timing,
                settle_at=settle_at,
            )
            if with_row is not None:
                await connection.execute(with_row[0], (placed, *with_row[1]))
        return placed, family

    async def _collapse_or_insert(
        self,
        connection: Connection,
        job_type: str,
        serialized: str,
        row: tuple[Any, ...],
        *,
        priority: int,
        requested_by: str | None,
        timing: str | None,
        settle_at: int | None = None,
    ) -> tuple[str, str | None]:
        """`dedupe`, inside a write the caller holds: the id of an identical job already waiting,
        strengthened to this request, or this row inserted, its own id and the family it joined.

        `settle_at` is a settle's moment (`enqueue_when_settled`): collapsing onto a waiting settle
        puts it off to then, within `SETTLE_LONGEST_SECONDS` of when it was first asked for."""
        now = row[7]  # the row's own `created_at`: one moment for the insert and any collapse
        waiting = list(await connection.execute_fetchall(_PENDING_LIKE, (job_type, serialized)))
        if waiting:
            already = str(waiting[0]["id"])
            # A COLLAPSE MUST NOT MAKE EITHER CALLER'S REQUEST WEAKER: the row runs at the more
            # urgent of the two priorities, so a press landing on a watcher's row does not wait
            # behind the whole queue. Only ever downwards.
            if int(waiting[0]["priority"]) > priority:
                await connection.execute(_RAISE_PRIORITY, (priority, now, already))
                log.info(
                    "job.enqueue_deduped_raised",
                    job_type=job_type,
                    job_id=already,
                    priority=priority,
                )
            # AND IT MUST NOT LOSE WHO ASKED: a press landing on a scheduled row is still that
            # person's press.
            if requested_by is not None and waiting[0]["requested_by"] is None:
                await connection.execute(_NAME_REQUESTER, (requested_by, now, already))
            # AND A PRESS THAT MEANS NOW IS NOW, never held to quiet hours.
            if timing == AT_NOW and (
                waiting[0]["timing"] != AT_NOW or waiting[0]["run_after"] is not None
            ):
                await connection.execute(_RUN_NOW, (now, already))
                self._work_arrived([Arrival(job_type, already, AT_NOW, None)])
            # AND A SETTLE IS A MINUTE AFTER THE LAST FILE, NOT THE FIRST, so the pass runs once
            # the batch has landed.
            elif settle_at is not None:
                latest = int(waiting[0]["created_at"]) + SETTLE_LONGEST_SECONDS
                later = min(settle_at, latest)
                await connection.execute(_PUT_OFF, (later, now, already, later))
            log.info("job.enqueue_deduped", job_type=job_type, job_id=already)
            return already, None
        return str(row[0]), await _place(connection, row)

    async def enqueue_when_settled(
        self,
        job_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        delay: int = BATCH_SETTLE_SECONDS,
        priority: int = DEFAULT_PRIORITY,
        requested_by: str | None = None,
    ) -> str:
        """Ask for whole-library work once the batch that asked for it has stopped arriving.

        A thousand files landing each make the same whole-library pass worth running once. The
        delay lets the batch finish before the pass looks; the dedupe collapses the batch's requests
        onto the one waiting; neither works alone. A row already RUNNING is not collapsed onto, as
        it has read what it will read. A pass that must never run twice at the same time says so
        where its handler is registered (`alone=True`). `priority` and `requested_by` pass through.

        A row already waiting until about then is left as it is, after a read and no write: a
        thousand files each asking move it once per `SETTLE_SLACK_SECONDS`, not once each.

        Asked from inside a job, it is as urgent as that job, past the type's own urgency unless
        the type keeps it (`keeps_urgency`): a settle a pressed run asks for runs during the run.
        """
        from sift.kernel.jobs.worker_pool import keeps_its_urgency, registered_urgency

        asking = ASKED_AT.get()
        ceiling = registered_urgency(job_type) or PRIORITY_MIN
        if asking is not None and not keeps_its_urgency(job_type):
            priority = min(priority, asking)
        elif asking is not None:
            priority = max(min(priority, asking), ceiling)
        elif priority < ceiling:
            log.info("job.urgency_held", job_type=job_type, asked=priority, urgency=ceiling)
            priority = ceiling
        if requested_by is None:
            waiting = await self._settle_still_waiting(job_type, payload, delay, priority)
            if waiting is not None:
                return waiting
        return await self.enqueue(
            job_type,
            payload,
            run_after=self._now() + delay,
            dedupe=True,
            priority=priority,
            requested_by=requested_by,
            settling=True,
        )

    async def _settle_still_waiting(
        self, job_type: str, payload: Mapping[str, Any] | None, delay: int, priority: int
    ) -> str | None:
        """The waiting row this settle would move by less than `SETTLE_SLACK_SECONDS`, or None.

        Admitted first, so a switched-off pass still refuses. Read while the row is waiting, so a
        claim of it can only come after this file's change was written.
        """
        urgency, _ = await self._admit(
            job_type,
            priority=priority,
            max_attempts=DEFAULT_MAX_ATTEMPTS,
            requested_by=None,
            at=None,
            parent_id=None,
            require_handler=True,
            held_to_urgency=False,
        )
        body = dict(payload or {})
        _check_payload(body)
        row = await self._db.fetch_one(_PENDING_LIKE, (job_type, json.dumps(body)))
        if row is None or int(row["priority"]) > urgency:
            return None
        # A row due now is never put off (`_PUT_OFF`), so collapsing onto it would change nothing.
        if row["run_after"] is None:
            return str(row["id"])
        wanted = min(self._now() + delay, int(row["created_at"]) + SETTLE_LONGEST_SECONDS)
        if int(row["run_after"]) < wanted - SETTLE_SLACK_SECONDS:
            return None
        return str(row["id"])

    async def settle_into(
        self, job_types: Sequence[str], *, priority: int = BACKGROUND_PRIORITY
    ) -> None:
        """Ask for each of these whole-library passes once the batch asking has settled, skipping
        any that is switched off. What a file's work calls after making something those passes
        read: the duplicate sweep after a fingerprint, say. See `enqueue_when_settled`."""
        for job_type in job_types:
            try:
                await self.enqueue_when_settled(job_type, priority=priority)
            except JobSwitchedOff:
                log.info("job.settling_skipped", job_type=job_type, reason="switched off")

    async def retime_waiting(self, job_id: str, run_after: int) -> bool:
        """Move one waiting row nobody pressed to a new moment. False if it is not waiting any more.

        Moved rather than cancelled and queued again, so a schedule placed afresh at every start
        and every save leaves no pile of cancelled rows behind it on the Activity screen.
        """
        async with self._writing() as connection:
            rows = await _fetch(connection, _RETIME_WAITING, (run_after, self._now(), job_id))
        if rows:
            log.info("job.retimed", job_id=job_id, run_after=run_after)
        return bool(rows)

    async def set_waiting_units(self, job_id: str, units: int) -> bool:
        """Say how many files a job still waiting is about. False once it is not waiting."""
        async with self._writing() as connection:
            rows = await _fetch(
                connection, _SET_WAITING_UNITS, (max(0, units), self._now(), job_id)
            )
        return bool(rows)

    async def withdraw_waiting(self, job_type: str) -> list[str]:
        """Take back every row of this type that is waiting and that nobody pressed.

        What a schedule change does before it places the next run, so the old moment cannot run as
        well as the new. A press and a row with a parent are left alone. Cancelled rather than
        deleted, so Activity can say what happened to it.
        """
        async with self._writing() as connection:
            rows = await _fetch(connection, _WITHDRAW_WAITING, (self._now(), job_type))
        ids = [str(row["id"]) for row in rows]
        if ids:
            log.info("job.withdrawn", job_type=job_type, job_count=len(ids))
        return ids
