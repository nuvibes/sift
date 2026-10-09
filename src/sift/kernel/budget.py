# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dividing the machine between the long passes, and stepping back while somebody is at it."""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping

#: A quarter leaves a person three quarters of the machine; the stored setting's default.
STEP_BACK_SHARE = 25

WHOLE_DEVICE = 100


def stepped_workers(full: int, percent: int) -> int:
    """Workers out of `full` at `percent`, rounded up so a small pool keeps one."""
    if full <= 1:
        return max(1, full)
    share = min(WHOLE_DEVICE, max(1, percent))
    return max(1, min(full, math.ceil(full * share / WHOLE_DEVICE)))


def tool_threads(cores: int, *, running: int, percent: int) -> int:
    """Threads one background tool may use when `running` tools share `percent` of `cores`."""
    share = min(WHOLE_DEVICE, max(1, percent))
    usable = max(1, cores * share // WHOLE_DEVICE)
    return max(1, usable // max(1, running))


def processor_rate(threads: int, cores: int) -> int:
    """`threads` of `cores` as the system's processor rate, for tools that ignore thread flags."""
    return max(1, min(10_000, threads * 10_000 // max(1, cores)))


def divide(
    *,
    workers: int,
    entitlements: Mapping[str, int],
    busy: Mapping[str, int],
    fixed: Collection[str] = frozenset(),
    waited_on: Collection[str] = frozenset(),
) -> dict[str, int]:
    """How many of each kind may run together; a share is a cap, never a reservation."""
    if workers <= 0:
        return dict.fromkeys(entitlements, 0)

    wanting = {kind for kind, count in busy.items() if count > 0 and entitlements.get(kind, 0) > 0}
    limited = {kind for kind in fixed if kind in entitlements}
    # A fixed kind with no work takes nothing: it is not a reservation.
    taken = sum(min(entitlements[kind], workers) for kind in wanting & limited)
    left = max(1, workers - taken)
    sharing = wanting - limited
    allowances: dict[str, int] = {}
    for kind, entitled in entitlements.items():
        if kind in waited_on:
            # Counted in `wanting` so the passes make room; never capped.
            continue
        if entitled <= 0:
            # Zero means stop, never "a little bit".
            allowances[kind] = 0
            continue
        if kind in limited:
            # A typed limit is never raised or cut: the label says the number.
            allowances[kind] = min(entitled, workers)
            continue
        against = sum(entitlements[other] for other in sharing | {kind})
        allowances[kind] = max(1, min(left, left * entitled // against))
    return allowances


def unfinished_by_type(counts: Mapping[str, int], kinds: Mapping[str, int]) -> dict[str, int]:
    """The queue's per-type counts for this budget's kinds; a kind never run reads as zero."""
    return {kind: counts.get(kind, 0) for kind in kinds}
