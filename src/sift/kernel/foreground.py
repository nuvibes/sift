# SPDX-License-Identifier: AGPL-3.0-or-later
"""The screens go first: a job's statement waits a moment while a request is being answered.

A request and a job's statements share the database's threads, its writer and the interpreter's
lock, and a request a person is waiting on should not queue behind work nobody is watching. The
wait is short and bounded, so a job is slowed while the screens are busy and never stopped.
"""

from __future__ import annotations

import asyncio

from sift.kernel.log import job_cost

#: The longest one statement of a job waits for the requests being answered.
YIELD_LONGEST_SECONDS = 0.1

_answering = 0
_answered: asyncio.Event | None = None


def request_began() -> None:
    """A request is being answered until `request_ended`."""
    global _answering, _answered
    if _answering == 0:
        _answered = asyncio.Event()
    _answering += 1


def request_ended() -> None:
    global _answering
    _answering = max(0, _answering - 1)
    if _answering == 0 and _answered is not None:
        _answered.set()


def answering() -> int:
    """How many requests are being answered now."""
    return _answering


async def screens_first(writing: bool) -> None:
    """Inside a job, wait while a request is answered, `YIELD_LONGEST_SECONDS` at the longest.
    Never inside a write block (`writing`), which would hold the writer for the requests."""
    answered = _answered
    if _answering == 0 or writing or answered is None or job_cost() is None:
        return
    try:
        await asyncio.wait_for(answered.wait(), YIELD_LONGEST_SECONDS)
    except TimeoutError:
        return
