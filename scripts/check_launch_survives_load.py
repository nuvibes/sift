# SPDX-License-Identifier: AGPL-3.0-or-later
"""Launch a program a hundred times with numeric pools loaded; a hang is the fault (forks only)."""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time

from sift.kernel.subprocess import SubprocessError, run

#: Above anything a loaded machine does honestly: the fault is a launch that never returns.
PATIENCE_SECONDS = 5.0

ROUNDS = 100

#: Large enough that the library spreads it across its threads; an idle pool reproduces nothing.
MATRIX = 512


def _load_the_pools() -> object:
    """Import the numeric library; it must load after `sift` pinned its thread pools, as above."""
    import numpy

    return numpy


async def _one_launch(patience: float) -> tuple[float, str | None]:
    """Start a trivial program and wait for it; returns how long it took and what went wrong."""
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
        # Work first, then launch: the deadlock needs the pools busy.
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
