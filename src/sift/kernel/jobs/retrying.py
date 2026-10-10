# SPDX-License-Identifier: AGPL-3.0-or-later
"""When a failed job tries again: broken bytes never, a disk, share or tool fault after a wait."""

from __future__ import annotations

import errno
import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel (the error class only)

from sift.kernel.jobs.queue_rows import JobHeld

#: The first retry's wait, and the longest any retry waits, in seconds.
FIRST_WAIT = 30.0
LONGEST_WAIT = 600.0

#: How long a job waits for room on a full disk before it looks again, attempt handed back.
ROOM_WAIT = 600.0

_BACKS_OFF: set[str] = set()

#: Windows says a full disk as 112 (and 39 for a full handle); everywhere else it is ENOSPC.
_FULL_DISK_WINERRORS = frozenset({39, 112})

_FULL_DISK_SAID = ("no space left on device", "not enough space on the disk", "disk is full")


class WaitingForSpace(JobHeld):
    """A disk Sift writes to is full: the job waits for room with its attempt handed back."""

    def __init__(self, message: str, *, retry_in: float = ROOM_WAIT) -> None:
        super().__init__(message, retry_in=retry_in)


def backs_off(job_type: str) -> None:
    """Declare that a failed job of this type waits before its next try."""
    _BACKS_OFF.add(job_type)


def is_disk_full(error: BaseException) -> bool:
    """Whether this failure, or one it was raised from, is a disk with no room left."""
    seen: BaseException | None = error
    for _ in range(8):
        if seen is None:
            return False
        if isinstance(seen, OSError) and (
            seen.errno == errno.ENOSPC or getattr(seen, "winerror", None) in _FULL_DISK_WINERRORS
        ):
            return True
        if any(said in str(seen).lower() for said in _FULL_DISK_SAID):
            return True
        seen = seen.__cause__ or seen.__context__
    return False


def cannot_change(error: BaseException) -> bool:
    """Whether no retry can change this failure: a decoder said the bytes themselves are broken."""
    # Here, not at the top: the media module is heavy and the queue never needs it otherwise.
    from sift.kernel.media import is_broken_data

    return is_broken_data(str(error))


def _waits(error: BaseException) -> bool:
    """A failure of the disk, the share, a tool that hung or the database being busy."""
    # Here, not at the top: a bare interpreter loads this package without the tool runner.
    from sift.kernel.subprocess import TookTooLong

    if isinstance(error, TookTooLong):
        return True
    if isinstance(error, sqlite3.OperationalError):
        said = str(error)
        return "locked" in said or "busy" in said or "disk" in said
    return isinstance(error, OSError)


def backoff(job_type: str, attempts: int, error: BaseException | None = None) -> float | None:
    """Seconds before the next try after this many, or None for a failure retried immediately."""
    if error is not None and is_disk_full(error):
        return LONGEST_WAIT
    if job_type not in _BACKS_OFF and (error is None or not _waits(error)):
        return None
    return min(FIRST_WAIT * 2.0 ** max(0, attempts - 1), LONGEST_WAIT)
