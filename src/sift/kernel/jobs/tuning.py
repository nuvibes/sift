# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every number the queue is paced by; some must agree with each other, which a test asserts."""

from __future__ import annotations

IDLE_POLL_SECONDS = 1.0

HEARTBEAT_SECONDS = 15.0

#: Several heartbeats, so a slow disk never gets a healthy job reclaimed and run twice.
STALE_AFTER_SECONDS = 120

SWEEP_INTERVAL_SECONDS = 30

SETTLED_RETENTION_SECONDS = 7 * 24 * 60 * 60

#: This discards work, so it is far longer than a login or a fetch takes.
PARKED_RETENTION_SECONDS = 30 * 24 * 60 * 60

#: Bounded because the writer is single: a whole-table delete stalls every job's progress.
PRUNE_BATCH = 500

CLEAR_BATCH = 2000

#: The desktop's `STOP_TIMEOUT_MS` must allow longer; a test asserts it.
SHUTDOWN_GRACE_SECONDS = 30.0

PROGRESS_INTERVAL_SECONDS = 1.0

RECONFIGURE_SECONDS = 3.0


BATCH_SETTLE_SECONDS = 60

SETTLE_LONGEST_SECONDS = 15 * 60


DEFAULT_PRIORITY = 100

#: Ahead of whole-library passes, so a person waits for at most one job.
WAITED_ON_PRIORITY = 50

BACKGROUND_PRIORITY = 200

DEFAULT_MAX_ATTEMPTS = 3


MAX_ATTEMPTS_CEILING = 10

PRIORITY_MIN = 0
PRIORITY_MAX = 1000

MAX_ERROR_CHARACTERS = 2000
