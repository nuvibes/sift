# SPDX-License-Identifier: AGPL-3.0-or-later
"""How long a failed job waits before its next try, for the types that declare it.

A download that failed on a dropped connection and is tried again the same second fails again for
the same reason. A type that fetches over the network declares it beside its handler, and each
retry waits twice as long as the one before; every other type is retried immediately.
"""

from __future__ import annotations

#: The first retry's wait, and the longest any retry waits, in seconds.
FIRST_WAIT = 30.0
LONGEST_WAIT = 600.0

_BACKS_OFF: set[str] = set()


def backs_off(job_type: str) -> None:
    """Declare that a failed job of this type waits before its next try."""
    _BACKS_OFF.add(job_type)


def backoff(job_type: str, attempts: int) -> float | None:
    """Seconds before the next try after this many, or None for a type retried immediately."""
    if job_type not in _BACKS_OFF:
        return None
    return min(FIRST_WAIT * 2.0 ** max(0, attempts - 1), LONGEST_WAIT)
