# SPDX-License-Identifier: AGPL-3.0-or-later
"""The screens go first: a job's statement waits a moment while a request's own statement runs.

The wait is short and bounded (a job is slowed while the screens are busy, never stopped), asked
only while a request's statement runs or is queued, written on the job's record (`screens_wait_ms`),
and held to a share of the job's own time (`YIELD_SHARE`) so many statements do not each wait out
a busy screen.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from weakref import WeakKeyDictionary

from sift.kernel.log import JobCost, job_cost

#: The longest one statement of a job waits for the requests being answered.
YIELD_LONGEST_SECONDS = 0.1

#: The most of a job's own time it gives the screens, beyond its first wait.
YIELD_SHARE = 0.1

#: The shortest wait asked: below a tick of Windows' timer, a wait sleeps the whole tick anyway.
YIELD_SHORTEST_SECONDS = 0.016

#: Seconds each running job has waited for the screens, or set aside for a wait under way.
_YIELDED: WeakKeyDictionary[JobCost, float] = WeakKeyDictionary()

#: True in the task answering a request, so its statements count as a screen's.
_SERVING: ContextVar[bool] = ContextVar("sift_serving", default=False)
_answering = 0
#: A request's statements running or queued now, and the event set when none is.
_statements = 0
_statements_done: asyncio.Event | None = None


def request_began() -> None:
    """A request is being answered until `request_ended`."""
    global _answering
    _answering += 1
    _SERVING.set(True)


def request_ended() -> None:
    global _answering
    _answering = max(0, _answering - 1)
    _SERVING.set(False)


def answering() -> int:
    """How many requests are being answered now."""
    return _answering


def statements_answering() -> int:
    """How many of the requests' statements are running or queued now."""
    return _statements


@contextmanager
def a_requests_statement() -> Iterator[None]:
    """Around every statement: counted while it runs if a request's, nothing otherwise."""
    global _statements, _statements_done
    if not _SERVING.get():
        yield
        return
    if _statements == 0:
        _statements_done = asyncio.Event()
    _statements += 1
    try:
        yield
    finally:
        _statements -= 1
        if _statements == 0 and _statements_done is not None:
            _statements_done.set()


async def screens_first(writing: bool) -> None:
    """Inside a job, wait while a request's statement runs, `YIELD_LONGEST_SECONDS` at the
    longest and within the job's `YIELD_SHARE`. Never inside a write block (`writing`), which would
    hold the writer for the requests."""
    done = _statements_done
    cost = job_cost()
    if _statements == 0 or writing or done is None or cost is None:
        return
    started = time.perf_counter()
    yielded = _YIELDED.get(cost, 0.0)
    allowed = min(
        YIELD_LONGEST_SECONDS,
        YIELD_LONGEST_SECONDS + YIELD_SHARE * (started - cost.began) - yielded,
    )
    if allowed < YIELD_SHORTEST_SECONDS:
        return
    # Set aside before the wait: a job's parts run together, and each would see the same balance.
    _YIELDED[cost] = yielded + allowed
    try:
        await asyncio.wait_for(done.wait(), allowed)
    except TimeoutError:
        pass
    finally:
        ended = time.perf_counter()
        _YIELDED[cost] += (ended - started) - allowed
        cost.yielded(started, ended)
