# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every number the queue is paced by, in one place.

None is a setting: a setting is a promise that any value keeps working, and nobody has a reason to
turn these (how many jobs run at once is the machine's, and is `SIFT_WORKER_CONCURRENCY`). Some are
only correct in relation to each other, which a config file cannot say and a test here asserts: a
heartbeat slower than the watchdog's patience reclaims healthy jobs and runs them twice.
"""

from __future__ import annotations

# --- Pacing ------------------------------------------------------------------------------

#: How long an idle worker waits before asking again: immediate to a person, not eight queries a
#: second for ever.
IDLE_POLL_SECONDS = 1.0

#: How often a running job says it is still alive.
HEARTBEAT_SECONDS = 15.0

#: How quiet a heartbeat goes before the job is presumed dead and given to someone else. Several
#: beats of `HEARTBEAT_SECONDS`, generously, so a slow disk or a busy loop is never mistaken for a
#: dead worker and a job run twice; a test asserts the pair agrees.
STALE_AFTER_SECONDS = 120

#: How often the watchdog looks for jobs whose heartbeat has gone quiet.
SWEEP_INTERVAL_SECONDS = 30

#: How long a `done` or `canceled` job stays before the watchdog prunes it. The table is the queue's
#: whole memory and grows by thousands a day; a week is a judgement about how long the history is
#: read. Failures are never swept (a failure is the only record that something did not happen), nor
#: is each type's newest run (`_PRUNE_SETTLED`).
SETTLED_RETENTION_SECONDS = 7 * 24 * 60 * 60

#: How long a parked (`blocked`) job is kept before the prune takes it. A park waits for a login or
#: a model's weights and is released by that act, but nothing else bounds the pile. This DISCARDS
#: work, so it is four times the settled window: far longer than a login or a fetch takes, and the
#: watchdog logs the count it takes.
PARKED_RETENTION_SECONDS = 30 * 24 * 60 * 60

#: The most rows one prune pass removes: the writer is single, so a whole-table delete holds every
#: job's progress behind housekeeping. The pass converges over the watchdog's timer.
PRUNE_BATCH = 500

#: The most rows one pass of a person's Clear removes: larger than `PRUNE_BATCH` because somebody is
#: waiting for the rows to be gone, still a batch so the writer is given back between passes.
CLEAR_BATCH = 2000

#: How long shutdown waits for running jobs. The desktop's `STOP_TIMEOUT_MS` must allow longer (a
#: test asserts it); an unfinished job is re-run at the next boot either way.
SHUTDOWN_GRACE_SECONDS = 30.0

#: How often a looping handler may write progress (`JobContext.report_progress`): each report is a
#: write on the one write connection, and once a second is as often as a bar is looked at.
PROGRESS_INTERVAL_SECONDS = 1.0

#: How often the pool re-reads the performance settings: a few seconds is imperceptible after a
#: drag, and a poll cannot miss an update the way a pushed wire can.
RECONFIGURE_SECONDS = 3.0


#: How long a batch of per-file work settles before the whole-library work it asked for runs (face
#: grouping, near-duplicate comparison): every file landing in the minute collapses onto one deduped
#: request. One number here so the features cannot disagree.
BATCH_SETTLE_SECONDS = 60

#: The longest arriving files may keep pushing one settle back, from when it was first asked for: a
#: share that trickles all day would otherwise push it for ever.
SETTLE_LONGEST_SECONDS = 15 * 60

# --- Defaults for a job ------------------------------------------------------------------

#: Lower runs first. Jobs of equal priority run oldest-first.
DEFAULT_PRIORITY = 100

#: For work a person is waiting on: arrival order would put a pasted download behind a library-wide
#: pass, and per-type caps cannot help when no worker is ever free. It does not preempt; it bounds
#: the wait to one job.
WAITED_ON_PRIORITY = 50

#: For a whole-library catch-up pass nobody is waiting on, so it runs after everything else. `alone`
#: stops one pass starting twice; this stops any starting while somebody waits. They are queued by
#: `enqueue_when_settled`, after a burst ends, so they do not starve.
BACKGROUND_PRIORITY = 200

DEFAULT_MAX_ATTEMPTS = 3


# --- Ceilings --------------------------------------------------------------------------------
#
# Backstops for the slices that call `enqueue`: a user-influenced value a slice forwarded past its
# own validation cannot make a job retry for ever or jump the whole queue.

#: A job that failed this many times is stuck, not unlucky. Enqueuing above it is refused.
MAX_ATTEMPTS_CEILING = 10

#: The band a priority is held to, so a value past a slice's checks cannot run before everything or
#: never.
PRIORITY_MIN = 0
PRIORITY_MAX = 1000

#: How much of a failure is kept, from the END, where a tool says why: unbounded, a tool's standard
#: error sits in the row, the backup and the diagnostics export.
MAX_ERROR_CHARACTERS = 2000
