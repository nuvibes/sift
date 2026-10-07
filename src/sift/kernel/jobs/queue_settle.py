# SPDX-License-Identifier: AGPL-3.0-or-later
"""Finishing and running: done, failed, parked, held, paused and resumed, the heartbeat, the hand-back."""

from __future__ import annotations

from sift.kernel.jobs.queue_handoffs import HandOffs
from sift.kernel.jobs.queue_rows import (
    PAUSE_WITHDRAWN,
    STOP_TO_PAUSE,
    Beat,
    JobState,
    _fetch,
    _for_the_record,
)
from sift.kernel.log import get_logger
from sift.kernel.presses import Pressed, record_pressed

log = get_logger("sift.kernel.jobs.queue")
# `progress` is left alone when the job has children: the number shown for it is theirs.
_COMPLETE = """
UPDATE jobs
   SET state = 'done',
       stop_wanted = NULL,
       progress = CASE
           WHEN EXISTS (SELECT 1 FROM jobs child WHERE child.parent_id = jobs.id) THEN progress
           ELSE 1.0
       END,
       claimed_by = NULL,
       heartbeat_at = NULL,
       error = NULL,
       updated_at = ?
 WHERE id = ? AND state = 'running' AND claimed_by = ?
RETURNING id, parent_id, type, started_at, note, requested_by
"""

#: A benchmark's pause, kept on the paused row so a boot after a run cut short resumes it.
PAUSED_FOR_BENCHMARK = "benchmark"

#: Rows paused or started again for a benchmark per write, so no other writer waits long.
PAUSE_CHUNK = 2_000

# Retry or give up, decided in the statement rather than a read-then-write. A pause that was asked
# for wins over both, read from the row rather than the worker's memory so a pause asked between the
# last heartbeat and the raise counts; its attempt goes back and its error goes.
_FAIL = """
UPDATE jobs
   SET state = CASE
           WHEN stop_wanted IN ('pause', 'benchmark') THEN 'paused'
           WHEN attempts >= max_attempts THEN 'failed'
           ELSE 'queued'
       END,
       claimed_by = NULL,
       heartbeat_at = NULL,
       attempts = CASE
           WHEN stop_wanted IN ('pause', 'benchmark') THEN MAX(attempts - 1, 0) ELSE attempts
       END,
       error = CASE WHEN stop_wanted IN ('pause', 'benchmark') THEN NULL ELSE ? END,
       stop_wanted = CASE WHEN stop_wanted = 'benchmark' THEN stop_wanted END,
       updated_at = ?
 WHERE id = ? AND state = 'running' AND claimed_by = ?
RETURNING state, id, parent_id, type, started_at, note, requested_by, error
"""

# No retry can fix this, so it fails immediately. A pause asked meanwhile does not save it: its
# Resume would fail again for the same reason.
_FAIL_PERMANENTLY = """
UPDATE jobs
   SET state = 'failed',
       claimed_by = NULL,
       heartbeat_at = NULL,
       stop_wanted = NULL,
       error = ?,
       updated_at = ?
 WHERE id = ? AND state = 'running' AND claimed_by = ?
RETURNING id, parent_id, type, started_at, note, requested_by, error
"""

# Waiting is not attempting, so the attempt taken at claim time is handed back.
_BLOCK = """
UPDATE jobs
   SET state = 'blocked',
       claimed_by = NULL,
       heartbeat_at = NULL,
       stop_wanted = NULL,
       error = ?,
       attempts = MAX(attempts - 1, 0),
       updated_at = ?
 WHERE id = ? AND state = 'running' AND claimed_by = ?
RETURNING id
"""

# Held for a while, for `JobHeld`: back in the line with its attempt handed back, not claimable
# before `run_after`. A pause asked for meanwhile wins, as it does over a failure.
_HOLD = """
UPDATE jobs
   SET state = CASE WHEN stop_wanted IN ('pause', 'benchmark') THEN 'paused' ELSE 'queued' END,
       claimed_by = NULL,
       heartbeat_at = NULL,
       stop_wanted = CASE WHEN stop_wanted = 'benchmark' THEN stop_wanted END,
       error = ?,
       attempts = MAX(attempts - 1, 0),
       run_after = ?,
       updated_at = ?
 WHERE id = ? AND state = 'running' AND claimed_by = ?
RETURNING id
"""

# Pausing a job nobody has started: immediately, as there is no handler to ask.
_PAUSE_QUEUED = (
    "UPDATE jobs SET state = 'paused', stop_wanted = ?, updated_at = ? "
    "WHERE id = ? AND state = 'queued' RETURNING id"
)

# Pausing a RUNNING job is a request beside the row, not a write to its state, which is what a
# cancel does. A benchmark's word never replaces a person's pause.
_ASK_TO_PAUSE = (
    "UPDATE jobs SET stop_wanted = ?, updated_at = ? "
    "WHERE id = ? AND state = 'running' AND (? = 'pause' OR COALESCE(stop_wanted, '') <> 'pause')"
    " RETURNING id"
)

# The landing, fenced on the claim, its attempt handed back for `_BLOCK`'s reason: `paused`, unless
# a Resume WITHDREW the request while the handler wound down (`_UNASK_PAUSE`).
_PAUSE_RUNNING = """
UPDATE jobs
   SET state = CASE WHEN stop_wanted = 'resume' THEN 'queued' ELSE 'paused' END,
       claimed_by = NULL,
       heartbeat_at = NULL,
       stop_wanted = CASE WHEN stop_wanted = 'benchmark' THEN stop_wanted END,
       attempts = MAX(attempts - 1, 0),
       updated_at = ?
 WHERE id = ? AND state = 'running' AND claimed_by = ?
RETURNING id
"""

# Starting it again: payload, attempts and priority kept; `run_after` and the old `error` cleared.
_RESUME = (
    "UPDATE jobs SET state = 'queued', error = NULL, stop_wanted = NULL, run_after = NULL, "
    "updated_at = ? WHERE id = ? AND state = 'paused' RETURNING id, parent_id"
)

_RESUME_AFTER_BENCHMARK = (
    "UPDATE jobs SET state = 'queued', error = NULL, stop_wanted = NULL, run_after = NULL, "
    "updated_at = ? WHERE id IN (SELECT id FROM jobs WHERE state = 'paused' "
    "AND stop_wanted = 'benchmark' LIMIT ?) RETURNING id, parent_id"
)

# Resuming a job whose pause has not landed yet withdraws the request; the handler then runs on or
# lands as queued (`_PAUSE_RUNNING`).
_UNASK_PAUSE = (
    "UPDATE jobs SET stop_wanted = 'resume', updated_at = ? "
    "WHERE id = ? AND state = 'running' AND stop_wanted IN ('pause', 'benchmark') RETURNING id"
)

#: The type is bound TWICE, both the same value: positional binding has no other way to say "every
#: type" when none is named.
_UNBLOCK = (
    "UPDATE jobs SET state = 'queued', error = NULL, updated_at = ? "
    "WHERE state = 'blocked' AND (? IS NULL OR type = ?) RETURNING id"
)

# The heartbeat also answers whether anybody is asking the job to stop: the only channel there is.
_HEARTBEAT = (
    "UPDATE jobs SET heartbeat_at = ?, updated_at = ? "
    "WHERE id = ? AND state = 'running' AND claimed_by = ? RETURNING stop_wanted"
)

_SET_PROGRESS = (
    "UPDATE jobs SET progress = ?, updated_at = ? "
    "WHERE id = ? AND state = 'running' AND claimed_by = ? RETURNING id"
)

_SET_UNITS = (
    "UPDATE jobs SET units = ?, updated_at = ? "
    "WHERE id = ? AND state = 'running' AND claimed_by = ? RETURNING id"
)

_SET_NOTE = (
    "UPDATE jobs SET note = ?, updated_at = ? "
    "WHERE id = ? AND state = 'running' AND claimed_by = ? RETURNING id"
)

# Reclaim, in two halves: attempts left goes back in the line, none left is failed. `? IS NULL`
# serves both callers: boot passes no cut-off (every running row is an orphan), the watchdog one.
# The two WHEREs are one predicate written twice and must stay so; a test asserts they agree.
_RECLAIM_EXHAUSTED = """
UPDATE jobs
   SET state = CASE WHEN stop_wanted IN ('pause', 'benchmark') THEN 'paused' ELSE 'failed' END,
       claimed_by = NULL,
       heartbeat_at = NULL,
       stop_wanted = CASE WHEN stop_wanted = 'benchmark' THEN stop_wanted END,
       error = CASE WHEN stop_wanted IN ('pause', 'benchmark') THEN error ELSE ? END,
       updated_at = ?
 WHERE state = 'running'
   AND (? IS NULL OR heartbeat_at IS NULL OR heartbeat_at < ?)
   AND attempts >= max_attempts
RETURNING id, parent_id
"""

# Handing work back on an orderly shutdown without charging an attempt; a crash still charges one.
_HANDED_BACK = "Interrupted by a restart and queued again. This did not count as an attempt."

# A job ASKED TO PAUSE lands `paused` here and in both reclaims, so "pause it, then quit" does not
# come back running, and its note is left alone.
_RELEASE_RUNNING = """
UPDATE jobs
   SET state = CASE WHEN stop_wanted IN ('pause', 'benchmark') THEN 'paused' ELSE 'queued' END,
       claimed_by = NULL,
       heartbeat_at = NULL,
       stop_wanted = CASE WHEN stop_wanted = 'benchmark' THEN stop_wanted END,
       attempts = MAX(attempts - 1, 0),
       note = CASE WHEN stop_wanted IN ('pause', 'benchmark') THEN note ELSE ? END,
       updated_at = ?
 WHERE state = 'running'
RETURNING id
"""

_RECLAIM_REQUEUE = """
UPDATE jobs
   SET state = CASE WHEN stop_wanted IN ('pause', 'benchmark') THEN 'paused' ELSE 'queued' END,
       claimed_by = NULL,
       heartbeat_at = NULL,
       stop_wanted = CASE WHEN stop_wanted = 'benchmark' THEN stop_wanted END,
       updated_at = ?
 WHERE state = 'running'
   AND (? IS NULL OR heartbeat_at IS NULL OR heartbeat_at < ?)
   AND attempts < max_attempts
RETURNING id
"""


class Settling(HandOffs):
    """Everything a running job's worker writes, and how its row lands."""

    async def complete(
        self, job_id: str, worker_id: str, *, pressed: Pressed | None = None
    ) -> bool:
        """Mark a running job done. False means the job was no longer this worker's to finish.

        `pressed` is who pressed the pass this job ran over one file, written in the same
        transaction (`kernel.presses`): a job marked done whose press is not recorded, or the
        other way round, would be a file's History naming the wrong person for its last look.
        """
        async with self._writing() as connection:
            now = self._now()
            rows = await _fetch(connection, _COMPLETE, (now, job_id, worker_id))
            if not rows:
                return False
            await self._roll_up(connection, [rows[0]["parent_id"]])
            await self._record_runs(connection, rows, "done")
            if pressed is not None:
                await record_pressed(connection, pressed, finished_at=int(now))

        log.info("job.done", job_id=job_id, worker_id=worker_id)
        await self._tell_settled(rows)
        return True

    async def fail(
        self, job_id: str, worker_id: str, error: str, *, permanent: bool = False
    ) -> JobState | None:
        """Record a failure. Returns the state the job landed in, or None if it was not ours.

        Attempts left: back to `queued`; none, or `permanent`: `failed`; asked to pause: `paused`,
        its attempt handed back (`_FAIL` decides all three). The message is scrubbed on its way in
        (`_for_the_record`).
        """
        message = _for_the_record(error)
        now = self._now()

        async with self._writing() as connection:
            statement = _FAIL_PERMANENTLY if permanent else _FAIL
            rows = await _fetch(connection, statement, (message, now, job_id, worker_id))
            if not rows:
                return None

            state = JobState.FAILED if permanent else JobState(rows[0]["state"])
            if state is JobState.FAILED:
                await self._roll_up(connection, [rows[0]["parent_id"]])
                await self._record_runs(connection, rows, "failed")

        if state is JobState.FAILED:
            await self._tell_settled(rows)

        if state is JobState.PAUSED:
            # Not a failure at all: somebody had asked this job to stop, and the handler stopped,
            # untidily, but it stopped. See `_FAIL`.
            log.info("job.paused", job_id=job_id, worker_id=worker_id)
            return state

        log.warning(
            "job.failed" if state is JobState.FAILED else "job.retrying",
            job_id=job_id,
            worker_id=worker_id,
            error=message,
        )
        return state

    async def block(self, job_id: str, worker_id: str, reason: str) -> bool:
        """Park a running job until someone logs in. False if it was not ours."""
        async with self._writing() as connection:
            rows = await _fetch(
                connection, _BLOCK, (_for_the_record(reason), self._now(), job_id, worker_id)
            )
        if not rows:
            return False

        log.info("job.blocked", job_id=job_id, worker_id=worker_id)
        return True

    async def hold(self, job_id: str, worker_id: str, reason: str, *, retry_in: float) -> bool:
        """Put a running job back in the line for `retry_in` seconds, its attempt handed back.
        See `JobHeld`. False if it was not ours."""
        now = self._now()
        async with self._writing() as connection:
            rows = await _fetch(
                connection,
                _HOLD,
                (_for_the_record(reason), int(now + retry_in), now, job_id, worker_id),
            )
        if not rows:
            return False
        log.info("job.held", job_id=job_id, worker_id=worker_id, retry_in=retry_in)
        return True

    async def unblock(self, *, job_type: str | None = None) -> list[str]:
        """Return blocked jobs to the queue, and say which ones went.

        A sign-in names no type: its key is what every blocked job waits for. A caller that has
        supplied ONE thing names the work waiting for it, so unrelated parked work is not claimed,
        run and parked again on every event.
        """
        async with self._writing() as connection:
            rows = await _fetch(connection, _UNBLOCK, (self._now(), job_type, job_type))

        released = [row["id"] for row in rows]
        if released:
            log.info("job.unblocked", job_count=len(released), job_type=job_type)
        return released

    async def pause(self, job_id: str, *, for_benchmark: bool = False) -> bool:
        """Stop a job somebody means to start again. False when there was nothing to stop.

        A waiting job is paused outright; a RUNNING one is asked (`_ASK_TO_PAUSE`) and lands
        `paused` when its attempt ends, or done if it ignores `stopping()`. One row, not a tree.
        `for_benchmark` marks the row (`PAUSED_FOR_BENCHMARK`).
        """
        now = self._now()
        word = PAUSED_FOR_BENCHMARK if for_benchmark else STOP_TO_PAUSE
        async with self._writing() as connection:
            rows = await _fetch(
                connection, _PAUSE_QUEUED, (word if for_benchmark else None, now, job_id)
            )
            asked = False
            if not rows:
                rows = await _fetch(connection, _ASK_TO_PAUSE, (word, now, job_id, word))
                asked = bool(rows)

        if not rows:
            return False
        log.info("job.pause", job_id=job_id, asked_to_stop=asked)
        if asked:
            # Running: the handler hears it now rather than at its next heartbeat.
            self._stop_asked([job_id])
        return True

    async def pause_running(self, job_id: str, worker_id: str, reason: str) -> bool:
        """Park a running job that was asked to pause, once its handler has stopped. False if it
        was not ours: the same fence every other write a worker makes carries."""
        async with self._writing() as connection:
            rows = await _fetch(connection, _PAUSE_RUNNING, (self._now(), job_id, worker_id))
        if not rows:
            return False

        log.info("job.paused", job_id=job_id, worker_id=worker_id, reason=reason)
        return True

    async def resume(self, job_id: str) -> bool:
        """Put a paused job back in the line, exactly as it was. False if it was not paused.

        The same job and not a new one: same payload, same attempts already spent, same priority.
        Only `run_after` and the interrupted attempt's `error` go (see `_RESUME`).
        """
        async with self._writing() as connection:
            rows = await _fetch(connection, _RESUME, (self._now(), job_id))
            if not rows:
                # Asked to pause and still running: withdraw the request. See `_UNASK_PAUSE`.
                unasked = await _fetch(connection, _UNASK_PAUSE, (self._now(), job_id))
                if not unasked:
                    return False
                log.info("job.pause_withdrawn", job_id=job_id)
                # Heard now for the same reason a pause is: a handler that read "pause" and is
                # about to wind up should read the withdrawal first if it can.
                self._stop_asked([job_id])
                return True
            await self._roll_up(connection, [rows[0]["parent_id"]])

        log.info("job.resumed", job_id=job_id)
        return True

    async def resume_after_benchmark(self, *, chunk: int = PAUSE_CHUNK) -> list[str]:
        """Start again every row a benchmark paused, after it or at the boot after it. Their ids."""
        resumed: list[str] = []
        while True:
            async with self._writing() as connection:
                rows = await _fetch(connection, _RESUME_AFTER_BENCHMARK, (self._now(), chunk))
                await self._roll_up(connection, [row["parent_id"] for row in rows])
            resumed += [str(row["id"]) for row in rows]
            if len(rows) < chunk:
                return resumed

    async def beat(self, job_id: str, worker_id: str) -> Beat | None:
        """Say the job is still alive, and hear back whether anything is asking it to stop.

        None means it is no longer this worker's: a worker whose job was requeued under it learns so
        here and stops. A `Beat` carrying a word is somebody asking it to stop (`_HEARTBEAT`).
        """
        now = self._now()
        async with self._db.write() as connection:
            rows = await _fetch(connection, _HEARTBEAT, (now, now, job_id, worker_id))
        if not rows:
            return None
        stop = rows[0]["stop_wanted"]
        # A withdrawn pause is not a request; the handler that never noticed it simply carries on.
        if stop is None or stop == PAUSE_WITHDRAWN:
            return Beat(stop=None)
        # A handler hears a benchmark's pause as any pause.
        return Beat(stop=STOP_TO_PAUSE if stop == PAUSED_FOR_BENCHMARK else str(stop))

    async def heartbeat(self, job_id: str, worker_id: str) -> bool:
        """Whether the job is still this worker's. `beat` above, for a caller that only asks that."""
        return await self.beat(job_id, worker_id) is not None

    async def set_progress(self, job_id: str, worker_id: str, fraction: float) -> bool:
        """Record how far along a job is, as a fraction between 0 and 1."""
        value = min(1.0, max(0.0, fraction))
        async with self._writing() as connection:
            rows = await _fetch(connection, _SET_PROGRESS, (value, self._now(), job_id, worker_id))
        return bool(rows)

    async def set_units(self, job_id: str, worker_id: str, units: int) -> bool:
        """Record how many files a running job is about, once the handler knows."""
        async with self._writing() as connection:
            rows = await _fetch(
                connection, _SET_UNITS, (max(0, units), self._now(), job_id, worker_id)
            )
        return bool(rows)

    async def set_note(self, job_id: str, worker_id: str, note: str) -> bool:
        """Record what a job did, in a sentence, for whoever asked for it.

        Fenced on the claim like every write a handler makes, and scrubbed and bounded exactly as an
        error is: a note is written to be read, so it is likelier than an error to name something.
        """
        async with self._writing() as connection:
            rows = await _fetch(
                connection, _SET_NOTE, (_for_the_record(note), self._now(), job_id, worker_id)
            )
        return bool(rows)

    async def release_running(self) -> list[str]:
        """Hand back every job still in flight, without charging it an attempt. Returns their ids.

        On the way out of an orderly shutdown, once the workers have had their grace: what is left
        was interrupted by the restart, not by anything wrong with it.
        """
        async with self._writing() as connection:
            rows = await _fetch(connection, _RELEASE_RUNNING, (_HANDED_BACK, self._now()))
        ids = [str(row["id"]) for row in rows]
        if ids:
            log.info("jobs.released", job_count=len(ids))
        return ids

    async def reclaim(self, *, stale_after: int | None, error: str) -> tuple[list[str], list[str]]:
        """Take running jobs back from workers that are not coming back.

        `stale_after` of None takes every running job (boot, where nothing can be running); a number
        takes those whose heartbeat has been quiet that long (the watchdog). Returns (requeued,
        failed): a job out of attempts is failed, since one that hangs its worker hangs the next.
        """
        now = self._now()
        stale_before = None if stale_after is None else now - stale_after

        async with self._writing() as connection:
            exhausted = await _fetch(
                connection,
                _RECLAIM_EXHAUSTED,
                (_for_the_record(error), now, stale_before, stale_before),
            )
            requeued = await _fetch(connection, _RECLAIM_REQUEUE, (now, stale_before, stale_before))
            await self._roll_up(connection, [row["parent_id"] for row in exhausted])

        return [row["id"] for row in requeued], [row["id"] for row in exhausted]
