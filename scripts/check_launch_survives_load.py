# SPDX-License-Identifier: AGPL-3.0-or-later
"""Launch a tool, over and over, while the numeric libraries are loaded, and time every launch.

## What this catches, and why nothing else can

A process holding a numeric thread pool cannot reliably start another program. Launching is a
fork-family call whatever mechanism is used, those pools install handlers that run across one, and
with a pool live the handlers can deadlock, which leaves a launch that never returns and a server
that answers nothing at all, not even a static file. With the pools running, most launches can
hang, through fork+exec and through posix_spawn alike; with one thread, none do.

The fix is a setting read once, when a numeric library loads, so it has to be in place FIRST, and
`tests/gates/test_numeric_threads_are_pinned.py` holds exactly that: the setting is written before
the import. **That is all it holds.** It reads source, it never starts a program, and it cannot tell
a pinned pool from a pinned pool whose pinning stopped working: a library that starts reading its
own setting later, a new pool nobody pinned, an interpreter that changed when the setting is read.
Every one of those leaves the gate green and the application frozen.

So this is the other half. It does not read anything: it loads the numeric libraries for real, in
one process, and then launches a real program a hundred times through Sift's own launch seam,
timing each one. A launch that does not come back is the whole fault.

## It has to be a loaded machine, which is why this is run by hand and not a test

The deadlock needs the pools to have work in them, so an idle process reproduces nothing. This runs
its own load (a matrix multiply per round, on every thread the pools will take) and even then it
is probabilistic: ten rounds can show nine failures, and a machine doing nothing else may need
more. It belongs on a machine that is already busy, where the clock is not the
constraint. A hundred rounds take a few seconds, with launches in tens of milliseconds, nothing
near the patience.

## AND ON WINDOWS IT CANNOT FAIL, WHICH IS NOT THE SAME AS PASSING

The fault is a fork-family call running a pool's registered handlers. Windows has no fork: a process
is started with `CreateProcess`, there are no such handlers, and nothing can deadlock across one. So
a green run here says only that starting programs works. It is not evidence that the pinning is
doing anything, because on this site there is nothing for it to do.

That is worth saying rather than quietly relying on: the pinning still matters wherever Sift runs on
a system that forks, which is anywhere it runs from source outside Windows. **Run this where a fork
happens**, on such a system from a checkout, or the check is measuring a site the fault cannot reach.

    python scripts/check_launch_survives_load.py [--runs 100] [--patience 5]

Exits non-zero if any launch did not come back inside its patience, and prints what it measured
either way. A launch that fails to START is not this fault and is reported separately.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time

from sift.kernel.subprocess import SubprocessError, run

#: The most a launch may take before this is a hang rather than a slow machine, in seconds.
#:
#: The fault is not a slow launch, it is one that NEVER RETURNS, so this only has to sit above
#: anything a loaded machine does honestly. Starting a trivial program is milliseconds; five seconds
#: is three orders of magnitude of headroom, and a check that cries wolf on a busy night is one that
#: gets ignored.
PATIENCE_SECONDS = 5.0

#: How many times to launch. Ten rounds can show the fault several times over; a hundred is cheap
#: here (the launches are milliseconds) and makes a rarer version of
#: the same deadlock visible rather than lucky.
ROUNDS = 100

#: How big a matrix to multiply between launches, to put work in the pools.
#:
#: Large enough that the numeric library really does spread it across its threads: a small one is
#: done on the calling thread and the pool stays empty, which is the idle case that reproduces
#: nothing. Small enough that the whole run is seconds rather than minutes.
MATRIX = 512


def _load_the_pools() -> object:
    """Import the numeric library and hand back something to make work with.

    ORDER IS THE WHOLE POINT AND IT IS NOT VISIBLE HERE. `sift/__init__` pins every numeric thread
    pool by setting four environment variables, and each pool reads its own once, when the library
    loads, so pinning after the import does nothing at all while looking exactly like it worked.
    That import has already happened: the module-level `from sift.kernel.subprocess import ...`
    above runs `sift/__init__` before this file's first statement. Nothing here may import numpy at
    the top of the file, and nothing may add a numpy import above the sift one, or this script would
    measure a process the application never runs, and it would FAIL, which reads as the fault.
    """
    import numpy

    return numpy


async def _one_launch(patience: float) -> tuple[float, str | None]:
    """Start a trivial program and wait for it. Returns how long it took, and what went wrong.

    The program is this interpreter, exiting immediately. Nothing is measured about what it DOES:
    the fault is in the starting, and a tool with real work in it would only add noise to the one
    number that matters.
    """
    began = time.perf_counter()
    try:
        result = await run([sys.executable, "-c", "pass"], time_limit=patience)
    except SubprocessError as failure:
        return time.perf_counter() - began, f"did not run at all: {failure}"
    took = time.perf_counter() - began
    if took >= patience:
        return took, "did not come back inside its patience"
    if result.returncode != 0:
        return took, f"exited {result.returncode}"
    return took, None


async def _measure(rounds: int, patience: float) -> int:
    numpy = _load_the_pools()
    work = numpy.random.rand(MATRIX, MATRIX)  # type: ignore[attr-defined]

    timings: list[float] = []
    hung: list[tuple[int, float, str]] = []
    began = time.perf_counter()
    for round_number in range(rounds):
        # Work FIRST, then launch. An empty pool is the case that never reproduces; the deadlock
        # needs the handlers to have something to run across.
        await asyncio.to_thread(lambda: work @ work)  # type: ignore[operator]
        took, wrong = await _one_launch(patience)
        timings.append(took)
        if wrong is not None:
            hung.append((round_number, took, wrong))
    whole = time.perf_counter() - began

    timings.sort()
    print(
        f"rounds {rounds}   whole run {whole:.1f}s   "
        f"launch median {statistics.median(timings) * 1000:.0f} ms   "
        f"worst {timings[-1] * 1000:.0f} ms   patience {patience:.1f}s"
    )
    if hung:
        for round_number, took, wrong in hung:
            print(f"FAIL: round {round_number} {wrong} ({took:.3f}s)")
        print(
            f"FAIL: {len(hung)} of {rounds} launches did not survive. A numeric thread pool is "
            f"live with more than one thread in it, so a fork-family call can deadlock, and the "
            f"application freezes answering nothing, including static files."
        )
        return 1
    print("ok: every launch came back")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch a tool repeatedly under numeric load.")
    parser.add_argument("--runs", type=int, default=ROUNDS, help="how many launches to make")
    parser.add_argument(
        "--patience",
        type=float,
        default=PATIENCE_SECONDS,
        help="the most one launch may take before it counts as hung",
    )
    args = parser.parse_args()
    return asyncio.run(_measure(args.runs, args.patience))


if __name__ == "__main__":
    raise SystemExit(main())
