# SPDX-License-Identifier: AGPL-3.0-or-later
"""The job queue.

Everything slow in Sift runs here, and the whole point of it is that it survives the power going
out. A job is a row in the database before it is anything else; the process running it is
disposable, and losing one costs at most the work done since it started.

A feature uses two things:

    register_handler("thumbnail", make_thumbnail, name="Generating thumbnail")  # at boot, once
    await queue.enqueue("thumbnail", {"asset_id": asset_id})

and never touches the `jobs` table itself. The kernel claims the row, calls the handler, times it,
records what happened, and puts it back if the worker running it dies.

Payloads carry ids, not paths: see `queue._check_payload`, which enforces it.
"""

from __future__ import annotations

from sift.kernel.jobs import schema
from sift.kernel.jobs.families import FAMILY_LABELS, LONG_PASSES, Family
from sift.kernel.jobs.queue import (
    CANCELABLE_STATES,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    STEP_COUNT_CAP,
    STOP_TO_CANCEL,
    STOP_TO_PAUSE,
    TERMINAL_STATES,
    Beat,
    Job,
    JobBlocked,
    JobCanceled,
    JobFailedPermanently,
    JobHeld,
    JobPage,
    JobPaused,
    JobQueue,
    JobState,
    StepCounts,
    StepsPage,
    TaskRun,
    UnknownJobType,
    WaitingForPassword,
    WorkKind,
    WorkSummary,
    folded_state,
    waits_for_password,
)
from sift.kernel.jobs.recovery import recover
from sift.kernel.jobs.schedules import (
    ScheduledTask,
    ScheduleError,
    get_schedule,
    register_schedule,
    registered_schedules,
    scheduled_job_types,
)
from sift.kernel.jobs.switchboard import JobSwitchedOff, Readiness, Switch, Switchboard
from sift.kernel.jobs.tuning import (
    BACKGROUND_PRIORITY,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_PRIORITY,
    HEARTBEAT_SECONDS,
    MAX_ERROR_CHARACTERS,
    SHUTDOWN_GRACE_SECONDS,
    STALE_AFTER_SECONDS,
    WAITED_ON_PRIORITY,
)
from sift.kernel.jobs.watchdog import run_watchdog, sweep
from sift.kernel.jobs.work_ahead import WorkAhead
from sift.kernel.jobs.worker_pool import (
    Handler,
    JobContext,
    SystemCapabilities,
    SystemSecrets,
    WorkerPool,
    by_itself_job_types,
    claim_rank,
    counted_as,
    family_of,
    held_for,
    hold_on,
    in_claim_order,
    job_name,
    register_handler,
    registered_alone,
    registered_families,
    registered_follows,
    registered_handlers,
    registered_job_names,
    registered_product_carriers,
    registered_urgency,
    unlisted_job_types,
)
from sift.kernel.jobs.workspaces import Workspaces

__all__ = [
    "BACKGROUND_PRIORITY",
    "CANCELABLE_STATES",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_PAGE_SIZE",
    "DEFAULT_PRIORITY",
    "FAMILY_LABELS",
    "HEARTBEAT_SECONDS",
    "LONG_PASSES",
    "MAX_ERROR_CHARACTERS",
    "MAX_PAGE_SIZE",
    "SHUTDOWN_GRACE_SECONDS",
    "STALE_AFTER_SECONDS",
    "STEP_COUNT_CAP",
    "STOP_TO_CANCEL",
    "STOP_TO_PAUSE",
    "TERMINAL_STATES",
    "WAITED_ON_PRIORITY",
    "Beat",
    "Family",
    "Handler",
    "Job",
    "JobBlocked",
    "JobCanceled",
    "JobContext",
    "JobFailedPermanently",
    "JobHeld",
    "JobPage",
    "JobPaused",
    "JobQueue",
    "JobState",
    "JobSwitchedOff",
    "Readiness",
    "ScheduleError",
    "ScheduledTask",
    "StepCounts",
    "StepsPage",
    "Switch",
    "Switchboard",
    "SystemCapabilities",
    "SystemSecrets",
    "TaskRun",
    "UnknownJobType",
    "WaitingForPassword",
    "WorkAhead",
    "WorkKind",
    "WorkSummary",
    "WorkerPool",
    "Workspaces",
    "by_itself_job_types",
    "claim_rank",
    "counted_as",
    "family_of",
    "folded_state",
    "get_schedule",
    "held_for",
    "hold_on",
    "in_claim_order",
    "job_name",
    "recover",
    "register_handler",
    "register_schedule",
    "registered_alone",
    "registered_families",
    "registered_follows",
    "registered_handlers",
    "registered_job_names",
    "registered_product_carriers",
    "registered_schedules",
    "registered_urgency",
    "run_watchdog",
    "scheduled_job_types",
    "schema",
    "sweep",
    "unlisted_job_types",
    "waits_for_password",
]
