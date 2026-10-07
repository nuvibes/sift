# SPDX-License-Identifier: AGPL-3.0-or-later
"""A set of threads kept for work somebody is waiting on.

Blocking work goes to threads, and one shared pool mixes sub-millisecond reads of a video
somebody is watching with decodes and model runs that hold a thread for minutes. Together they
make the video stutter while pages stay quick, which reads as a playback bug and is the pool. So
serving-path reads use threads background work cannot reach: no sizing question, the capacity is
simply not shared. `asyncio.to_thread` stays right everywhere else; the line is "is a person
waiting right now", the same one `subprocess.Priority` draws for launched tools.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

#: The fewest serving threads: each is a request mid-read, so roughly how many people may watch at
#: once, past what the smallest supported box could serve anyway.
MIN_SERVING_THREADS = 8

#: The most, however many cores: these wait on disks, so more than the cores is fine, but a number
#: without a ceiling is one nobody chose.
MAX_SERVING_THREADS = 32

#: The floor under the shared pool: short file operations happen outside jobs too.
MIN_SHARED_THREADS = 16

#: Threads on top of the workers' own, for everything that steps off the loop without being a job,
#: as the database's reader pool is sized: no bigger than the workers, everything else would queue.
SHARED_THREAD_HEADROOM = 8

#: Threads one worker can hold together: a tool is launched from one, and the file work either side
#: of it takes others. A tool's pipes are read on threads of its own (`subprocess._collect`), since
#: they are needed all together and a pool short of them stalls the tool on a full pipe.
THREADS_PER_WORKER = 3

_serving: ThreadPoolExecutor | None = None
_shared: ThreadPoolExecutor | None = None
_shared_size = 0


def serving_threads(cores: int) -> int:
    """How many threads to keep for work somebody is waiting on, on a machine with `cores` cores.

    From the hardware, never the worker count an admin can raise, which would grow it into the
    contention it exists to prevent.
    """
    return max(MIN_SERVING_THREADS, min(MAX_SERVING_THREADS, cores))


def open_serving_pool() -> None:
    """Create the pool. Called once, at start-up. Calling it twice is not an error and does not
    create a second one: the second call would otherwise strand the first pool's threads."""
    global _serving
    if _serving is not None:
        return
    _serving = ThreadPoolExecutor(
        max_workers=serving_threads(os.cpu_count() or 1),
        thread_name_prefix="sift-serving",
    )


def close_serving_pool() -> None:
    """Let the pool go at shutdown, without waiting on what is in it.

    Not waited on: holding shutdown for a read nobody will receive gets a container killed rather
    than stopped.
    """
    global _serving
    if _serving is None:
        return
    _serving.shutdown(wait=False, cancel_futures=True)
    _serving = None


def shared_threads(workers: int) -> int:
    """How big the pool everything else shares should be, for `workers` job workers.

    The language's default (`min(32, cores + 4)`) bears no relation to the worker count, which an
    admin can raise past it, making the pool the silent real limit. So it is sized from the workers
    who fill it, as the database's reader pool is, three each (`THREADS_PER_WORKER`).
    """
    return max(MIN_SHARED_THREADS, workers * THREADS_PER_WORKER + SHARED_THREAD_HEADROOM)


def size_shared_pool(workers: int) -> bool:
    """Make the shared pool the right size for `workers`. True when it actually changed.

    Called on the timer that reconfigures the workers, so the pool rises with them. A pool cannot
    be resized, so a new one replaces it and the old is let go WITHOUT cancelling its queue: those
    are real requests' reads and writes, and a settings change must not become errors on screen.
    """
    global _shared, _shared_size
    wanted = shared_threads(workers)
    if _shared is not None and wanted == _shared_size:
        return False
    replacing = _shared
    _shared = ThreadPoolExecutor(max_workers=wanted, thread_name_prefix="sift-shared")
    _shared_size = wanted
    asyncio.get_running_loop().set_default_executor(_shared)
    if replacing is not None:
        replacing.shutdown(wait=False)
    return True


def close_shared_pool() -> None:
    """Let the shared pool go at shutdown. Not waited on, for the reason the serving pool is not."""
    global _shared, _shared_size
    if _shared is None:
        return
    _shared.shutdown(wait=False)
    _shared = None
    _shared_size = 0


def waits_on_storage[**P, T](work: Callable[P, T]) -> Callable[P, T]:
    """Mark a synchronous function that waits on storage somebody else owns. Changes nothing.

    Read by `tests/gates/test_no_blocking_call_on_the_loop.py`, which refuses a marked call in async
    code without a thread; passing the name to `to_thread` costs nothing. A mark, because the gate
    stops at the module edge rather than guessing which same-named method a call meant, so a helper
    called from another module was invisible to it. Marked: waits on storage the user chose (a
    library root may be a dead share) or walks a whole folder; Sift's own local files are not, or
    the noise would get the check turned off.
    """
    return work


async def on_serving_thread[**P, T](work: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """Run `work` on a serving thread and hand back what it returned.

    Falls back to the ordinary pool when none is open, as in a test calling directly; never in a
    running install, which opens the pool before serving anything.
    """
    if _serving is None:
        return await asyncio.to_thread(work, *args, **kwargs)
    loop = asyncio.get_running_loop()
    if kwargs:
        return await loop.run_in_executor(_serving, lambda: work(*args, **kwargs))
    return await loop.run_in_executor(_serving, work, *args)
