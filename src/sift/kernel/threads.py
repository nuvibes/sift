# SPDX-License-Identifier: AGPL-3.0-or-later
"""Threads kept for work somebody is waiting on, so a background decode never stalls a video."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

#: Roughly how many people may watch together.
MIN_SERVING_THREADS = 8

#: These wait on disks, so more than the cores is fine, but not without a ceiling.
MAX_SERVING_THREADS = 32

MIN_SHARED_THREADS = 16

SHARED_THREAD_HEADROOM = 8

#: A tool's launch plus the file work either side of it.
THREADS_PER_WORKER = 3

_serving: ThreadPoolExecutor | None = None
_shared: ThreadPoolExecutor | None = None
_shared_size = 0


def serving_threads(cores: int) -> int:
    """Threads kept for waiting work, from the hardware, never the worker count an admin raises."""
    return max(MIN_SERVING_THREADS, min(MAX_SERVING_THREADS, cores))


def open_serving_pool() -> None:
    """Create the pool once at start-up; a second call keeps the first pool."""
    global _serving
    if _serving is not None:
        return
    _serving = ThreadPoolExecutor(
        max_workers=serving_threads(os.cpu_count() or 1),
        thread_name_prefix="sift-serving",
    )


def close_serving_pool() -> None:
    """Let the pool go at shutdown without waiting, so a container is stopped, not killed."""
    global _serving
    if _serving is None:
        return
    _serving.shutdown(wait=False, cancel_futures=True)
    _serving = None


def shared_threads(workers: int) -> int:
    """The shared pool's size, from the workers who fill it rather than the language default."""
    return max(MIN_SHARED_THREADS, workers * THREADS_PER_WORKER + SHARED_THREAD_HEADROOM)


def size_shared_pool(workers: int) -> bool:
    """Resize the shared pool for `workers`, never cancelling the old queue; True if changed."""
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
    """Mark a function that waits on storage someone else owns, for the blocking-call gate."""
    return work


async def on_serving_thread[**P, T](work: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """Run `work` on a serving thread, or the ordinary pool when none is open (tests)."""
    if _serving is None:
        return await asyncio.to_thread(work, *args, **kwargs)
    loop = asyncio.get_running_loop()
    if kwargs:
        return await loop.run_in_executor(_serving, lambda: work(*args, **kwargs))
    return await loop.run_in_executor(_serving, work, *args)
