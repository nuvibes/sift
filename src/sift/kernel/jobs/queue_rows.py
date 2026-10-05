# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a job is: its states, its row, the words a handler raises, and the payload rule."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sift.kernel.db import Connection, Row
from sift.kernel.jobs.tuning import MAX_ERROR_CHARACTERS
from sift.kernel.log import redact
from sift.kernel.paging import MAX_PAGE_SIZE as _MAX_PAGE_SIZE


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELED = "canceled"

    # Not a failure: the job needs a stored login, whose key exists only while an admin is signed
    # in, so it waits rather than burn a retry that no retry can fix.
    BLOCKED = "blocked"
    # Stopped by somebody who means to start it again: it keeps everything, unlike a cancel.
    PAUSED = "paused"


TERMINAL_STATES = frozenset({JobState.DONE, JobState.FAILED, JobState.CANCELED})

#: States a job can be cancelled out of; `paused` is one, or a Stop would leave paused rows.
CANCELABLE_STATES = frozenset(
    {JobState.QUEUED, JobState.RUNNING, JobState.BLOCKED, JobState.PAUSED}
)

#: What a running job is ASKED to do (`JobContext.stopping()`): pause keeps, cancel throws away.
STOP_TO_PAUSE = "pause"
STOP_TO_CANCEL = "cancel"
#: Not a request to stop: the mark left where a pause request WAS, when somebody resumed before
#: the handler stopped. A heartbeat never hands it out as a stop word. See `_UNASK_PAUSE`.
PAUSE_WITHDRAWN = "resume"


class JobBlocked(Exception):
    """Raised by a handler that needs a secret while nobody is signed in: the job waits in
    `blocked`, costing no attempt, until someone signs in."""


#: How every wait for the password begins: one string for the row, the unlock bar and the handler.
UNLOCK_WAIT = "Waiting for your password to unlock "


class WaitingForPassword(JobBlocked):
    """Raised by a handler whose saved secret is sealed until somebody gives the password; `what`
    is the thing sealed, as the sentence ends ("the stash-box keys")."""

    def __init__(self, what: str) -> None:
        super().__init__(f"{UNLOCK_WAIT}{what}.")


def waits_for_password(error: str | None) -> bool:
    """Whether a parked job's stored reason is a wait for the password. See `WaitingForPassword`."""
    return error is not None and error.startswith(UNLOCK_WAIT)


class JobHeld(Exception):
    """Raised by a handler whose work this machine cannot do just now, for a reason that passes on
    its own: the job goes back into the line with its attempt handed back, unclaimed for
    `retry_in` seconds."""

    def __init__(self, message: str, *, retry_in: float) -> None:
        super().__init__(message)
        self.retry_in = retry_in


class JobFailedPermanently(Exception):
    """Raised by a handler for a failure no retry can fix (a folder not plugged in, a file that
    does not decode): the job goes straight to `failed`, and its words are what the screen says."""


class JobCanceled(Exception):
    """Raised inside a handler whose job was cancelled, or taken from it, while it ran."""


class JobPaused(Exception):
    """Raised by a handler that has seen `stopping() == 'pause'` and wound itself up: the row goes
    to `paused` with everything kept. A handler that returns normally has finished instead."""


class UnknownJobType(Exception):
    """No handler is registered for this job type."""


@dataclass(frozen=True, slots=True)
class Job:
    """One row of the queue."""

    id: str
    parent_id: str | None
    type: str
    state: JobState
    priority: int
    payload: dict[str, Any]
    progress: float
    attempts: int
    max_attempts: int
    claimed_by: str | None
    heartbeat_at: int | None
    error: str | None
    #: What this job did, in a sentence, or None: `error` explains a failure, this a success.
    note: str | None
    #: The earliest this job may be claimed, or None for as soon as a worker is free.
    run_after: int | None
    created_at: int
    updated_at: int
    #: How many files this job is about: what "left" is weighed in (see `WorkKind`).
    units: int = 1
    #: When a worker picked this job up, or None if never claimed. Not `created_at`.
    started_at: int | None = None
    #: The user whose press queued this job, or None when nobody pressed anything.
    requested_by: str | None = None
    #: `now` for a press that runs at once, `quiet` for work held to quiet hours, None otherwise.
    timing: str | None = None


@dataclass(frozen=True, slots=True)
class TaskRun:
    """One run of a scheduled task: the job row that heads it and every row under that head, since
    a head that hands out work finishes long before its rows do (`_TASK_RUNS`)."""

    #: The job row that heads this run: what tells two runs apart, even two begun in one second.
    id: str
    #: When a worker picked the head up, or None; never `created_at`, a cadence before the run.
    started_at: int | None
    finished_at: int | None
    #: How it ended, folded over the family as Activity folds it, or running while any of it is.
    state: JobState
    #: What the run did: the note of the family's last row of the head's own type, or None.
    note: str | None
    runs_total: int
    #: Why a failed run failed, as the head's row records it, or None.
    error: str | None = None

    @property
    def seconds(self) -> int | None:
        """How long it took, or None while it is going and for a row with no start recorded."""
        if self.finished_at is None or self.started_at is None:
            return None
        return max(0, self.finished_at - self.started_at)


@dataclass(frozen=True, slots=True)
class LiveProducts:
    """Live rows of one type and state that name the same products, counted. See `_LIVE_PRODUCTS`."""

    type: str
    state: JobState
    quiet: bool
    products: tuple[str, ...]
    count: int


@dataclass(frozen=True, slots=True)
class LiveWork:
    """Live rows of one type naming the same products, split by whether a person pressed them."""

    type: str
    #: Work of a press for some files (the pressed row, or one its work handed out), never a pass
    #: over the library.
    pressed: bool
    #: The products their payload names, in its order.
    products: tuple[str, ...]
    #: Whether the payload asks for the work again where the file has it (`families.AGAIN`).
    again: bool
    count: int
    since: int
    #: Work of a run over some library folders (its top names `roots`); never also `pressed`.
    folders: bool = False


@dataclass(frozen=True, slots=True)
class PressedWork:
    """Rows of one type and state of the runs over some files, counted from a moment on."""

    type: str
    state: JobState
    products: tuple[str, ...]
    again: bool
    count: int
    folders: bool = False


@dataclass(frozen=True, slots=True)
class Beat:
    """What a heartbeat answers while the job is still the worker's (an object, since a tuple is
    always true): `stop` is the word somebody has asked it to stop for, or None."""

    stop: str | None


@dataclass(frozen=True, slots=True)
class JobPage:
    """A page of the queue, and how many rows the filter matched in total."""

    jobs: list[Job]
    total: int
    #: On a page of families, how many are in each state their folded row shows; the page's
    #: `total` is these added up, or the one state it was narrowed to. Empty on a page of rows.
    by_state: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StepCounts:
    """A family's steps counted by state, each count stopped at `STEP_COUNT_CAP` (`at_least`)."""

    by_state: dict[str, int]
    at_least: bool

    @property
    def steps(self) -> int:
        return sum(self.by_state.values())


@dataclass(frozen=True, slots=True)
class StepsPage:
    """A page of one family's steps in the order handed out; `total` stops at the cap."""

    jobs: list[Job]
    total: int
    at_least: bool


#: The one state a folded row shows, strongest first; FAILED before running, so a family folded to
#: one row never hides a failure. It reads Canceled only when its top was called off.
_FOLDED_STRONGEST_FIRST = (
    JobState.FAILED,
    JobState.RUNNING,
    JobState.PAUSED,
    JobState.BLOCKED,
    JobState.QUEUED,
)


def folded_state(own: JobState, steps: Mapping[str, int]) -> JobState:
    """The one state a top row shows for its whole family: its own and every step's together."""
    for state in _FOLDED_STRONGEST_FIRST:
        if own is state or steps.get(state.value, 0) > 0:
            return state
    if own is JobState.CANCELED:
        return JobState.CANCELED
    return JobState.DONE


@dataclass(slots=True)
class WorkKind:
    """What one kind of job has to do in the current run, and how fast it is going: counted WITHIN
    the run, so a bar describes the batch somebody is watching."""

    done: int = 0
    outstanding: int = 0
    failed: int = 0
    per_minute: float = 0.0
    left_units: float = 0.0
    """How many files the outstanding jobs still have in front of them: each job's units times
    the fraction it has not reported done. A kind's estimate is worked out from this where the
    library cannot be asked, so a scan holding thousands of files weighs thousands and not one."""
    """How many finished in the recent window, per minute; zero is "no estimate yet"."""


@dataclass(frozen=True, slots=True)
class WorkSummary:
    """Every number the dashboard draws its bars from, out of one pass over the table: `states`
    by kind and state, `run` the current batch, `since` when it began (None when nothing is left)."""

    states: dict[str, dict[str, int]]
    run: dict[str, WorkKind]
    since: int | None


@dataclass(slots=True)
class FilesToRead:
    """What the live walks still have to read, by media kind, and how many are not counted yet."""

    by_kind: dict[str, float] = field(default_factory=dict)
    uncounted: int = 0


#: The states a job is still going to be worked on in.
_UNFINISHED = frozenset(state.value for state in CANCELABLE_STATES)

DEFAULT_PAGE_SIZE = 50

#: The most rows one read of the queue returns: the kernel's one page ceiling, re-exported.
MAX_PAGE_SIZE = _MAX_PAGE_SIZE

#: `folded_state` as SQL, one expression for the tally and the page, held to it by a test.
_FOLDED = """CASE
 WHEN EXISTS (SELECT 1 FROM jobs AS step WHERE step.root_id = jobs.id AND step.state = 'failed')
  THEN 'failed'
 WHEN EXISTS (SELECT 1 FROM jobs AS step WHERE step.root_id = jobs.id AND step.state = 'running')
  THEN 'running'
 WHEN EXISTS (SELECT 1 FROM jobs AS step WHERE step.root_id = jobs.id AND step.state = 'paused')
  THEN 'paused'
 WHEN EXISTS (SELECT 1 FROM jobs AS step WHERE step.root_id = jobs.id AND step.state = 'blocked')
  THEN 'blocked'
 WHEN EXISTS (SELECT 1 FROM jobs AS step WHERE step.root_id = jobs.id AND step.state = 'queued')
  THEN 'queued'
 WHEN jobs.state = 'canceled' THEN 'canceled'
 ELSE 'done'
END"""


def _to_job(row: Row) -> Job:
    return Job(
        id=row["id"],
        parent_id=row["parent_id"],
        type=row["type"],
        state=JobState(row["state"]),
        priority=row["priority"],
        payload=json.loads(row["payload"]),
        progress=row["progress"],
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        claimed_by=row["claimed_by"],
        heartbeat_at=row["heartbeat_at"],
        error=row["error"],
        note=row["note"],
        run_after=row["run_after"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        units=int(row["units"]),
        started_at=None if row["started_at"] is None else int(row["started_at"]),
        requested_by=row["requested_by"],
        timing=row["timing"],
    )


# A payload carries ids, never places on disk: a path leaks, goes stale and can point anywhere.
_ABSOLUTE_PATH = re.compile(r"^(/|[A-Za-z]:[\\/]|\\\\)")
_PATH_SEPARATORS = re.compile(r"[/\\]")


def _looks_like_a_path(value: str) -> bool:
    """Whether a payload string is a file path rather than an id or a URL: absolute, `~`, or a
    whole `..` segment (`../../etc/passwd`), never a name like `a..b`."""
    if _ABSOLUTE_PATH.match(value) or value.startswith("~"):
        return True
    return ".." in _PATH_SEPARATORS.split(value)


def _check_payload(value: object, where: str = "payload") -> None:
    if isinstance(value, str):
        if _looks_like_a_path(value):
            raise ValueError(
                f"{where} looks like a file path. A job payload carries ids (an asset id, a "
                "library-root id, a download id) and the handler resolves the path from them."
            )
    elif isinstance(value, dict):
        for key, item in value.items():
            _check_payload(item, f"{where}[{key!r}]")
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _check_payload(item, f"{where}[{index}]")


async def _fetch(connection: Connection, sql: str, params: Sequence[Any]) -> list[Row]:
    """`execute_fetchall`, as a list. Every statement here returns its rows, so they are read."""
    return list(await connection.execute_fetchall(sql, params))


def _for_the_record(message: str) -> str:
    """What a failure or a wait may say once it is stored: scrubbed of names whatever the log
    toggle says (it travels in backups and exports), and cut to its END, where tools put the reason."""
    scrubbed = str(redact(message, always_personal=True))
    if len(scrubbed) <= MAX_ERROR_CHARACTERS:
        return scrubbed
    return "(truncated) ..." + scrubbed[-MAX_ERROR_CHARACTERS:]


def _products_named(value: object) -> tuple[str, ...]:
    """The products a grouped read's `products` JSON names; anything but a list of names, none."""
    try:
        named = json.loads(value) if isinstance(value, str) else []
    except ValueError:
        named = []
    return (
        tuple(str(one) for one in named if isinstance(one, str)) if isinstance(named, list) else ()
    )
