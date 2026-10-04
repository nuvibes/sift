# SPDX-License-Identifier: AGPL-3.0-or-later
"""The threads kept for work somebody is waiting on: background work cannot take them. The test
that matters fills the ordinary pool and shows a serving read going through anyway."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest

from sift.kernel import threads


@pytest.fixture(autouse=True)
def no_pool_left_open() -> Iterator[None]:
    """No test leaves a pool open for the next."""
    threads.close_serving_pool()
    threads.close_shared_pool()
    yield
    threads.close_serving_pool()
    threads.close_shared_pool()


# --- the pool everything else shares


def test_the_shared_pool_is_bigger_than_the_workers_that_fill_it() -> None:
    """The shared pool is bigger than the workers, or everything else queues behind jobs."""
    assert threads.shared_threads(8) > 8
    assert threads.shared_threads(64) > 64


def test_a_small_worker_count_still_gets_a_floor() -> None:
    """A one-worker machine still gets more than one thread to step off the loop with."""
    assert threads.shared_threads(1) == threads.MIN_SHARED_THREADS


async def test_sizing_the_shared_pool_takes_effect_and_says_when_it_changed() -> None:
    assert threads.size_shared_pool(8) is True
    assert threads._shared_size == threads.shared_threads(8)

    # The same number again: no new pool, since replacing one costs every thread.
    assert threads.size_shared_pool(8) is False

    assert threads.size_shared_pool(40) is True
    assert threads._shared_size == threads.shared_threads(40)


async def test_work_already_handed_to_the_old_pool_still_finishes() -> None:
    """Work queued in the replaced pool still finishes: a resize must not error real requests."""
    threads.size_shared_pool(4)
    running = threads._shared
    assert running is not None
    in_flight = running.submit(lambda: "finished anyway")

    threads.size_shared_pool(40)

    assert in_flight.result(timeout=5) == "finished anyway"


async def test_the_loop_uses_the_pool_that_was_sized() -> None:
    """`asyncio.to_thread` lands in the sized pool."""
    threads.size_shared_pool(8)

    assert (await asyncio.to_thread(threading.current_thread)).name.startswith("sift-shared")


def test_a_small_machine_still_gets_enough_to_serve_with() -> None:
    """Below the floor the serving count stops falling, or a third request waits on two."""
    assert threads.serving_threads(1) == threads.MIN_SERVING_THREADS
    assert threads.serving_threads(2) == threads.MIN_SERVING_THREADS


def test_a_bigger_machine_gets_more_up_to_a_ceiling() -> None:
    assert threads.serving_threads(16) == 16
    assert threads.serving_threads(128) == threads.MAX_SERVING_THREADS


def test_the_size_does_not_come_from_the_job_workers() -> None:
    """The serving size takes cores and nothing else: grown with the worker setting, the sweep it
    is kept from would take it."""
    assert threads.serving_threads.__code__.co_varnames[:1] == ("cores",)


async def test_work_runs_and_answers_when_no_pool_is_open() -> None:
    """With no pool open (a test, a script) work runs on the ordinary pool."""
    assert await threads.on_serving_thread(len, "abcd") == 4


async def test_work_runs_and_answers_with_the_pool_open() -> None:
    threads.open_serving_pool()

    assert await threads.on_serving_thread(len, "abcde") == 5


async def test_keyword_arguments_reach_the_work() -> None:
    """Both paths through `on_serving_thread` pass keyword arguments."""
    threads.open_serving_pool()

    def add(first: int, *, second: int) -> int:
        return first + second

    assert await threads.on_serving_thread(add, 2, second=3) == 5


async def test_the_work_really_leaves_the_event_loop() -> None:
    """The work runs on a thread, not inline on the loop."""
    threads.open_serving_pool()
    here = threading.get_ident()

    assert await threads.on_serving_thread(threading.get_ident) != here


def test_opening_twice_does_not_strand_a_pool() -> None:
    """Opening twice does not strand the first pool's threads."""
    threads.open_serving_pool()
    first = threads._serving
    threads.open_serving_pool()

    assert threads._serving is first


def test_closing_when_nothing_is_open_is_not_an_error() -> None:
    threads.close_serving_pool()
    threads.close_serving_pool()


async def test_a_serving_read_goes_through_while_the_ordinary_pool_is_full() -> None:
    """With the ordinary pool's one thread taken, a serving read goes through and the same read on
    the ordinary pool does not, so the pool really was full."""
    loop = asyncio.get_running_loop()
    occupied = asyncio.Event()
    release = threading.Event()

    # Left for the loop's own teardown, which may still hand work to its default pool.
    only_one = ThreadPoolExecutor(max_workers=1)
    loop.set_default_executor(only_one)

    def hold() -> None:
        # Signalled through the loop, which is where the flag is read.
        loop.call_soon_threadsafe(occupied.set)
        release.wait(timeout=5.0)

    holding = loop.run_in_executor(only_one, hold)
    await occupied.wait()

    threads.open_serving_pool()
    try:
        assert await asyncio.wait_for(threads.on_serving_thread(len, "abc"), timeout=1.0) == 3

        # The control: the ordinary pool is really full.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.to_thread(len, "abc"), timeout=0.2)
    finally:
        release.set()
        await holding
