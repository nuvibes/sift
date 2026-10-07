# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dividing the machine between the long passes that want all of it, and how much of it they have.

While somebody is at the device, background work steps back to a share of it, bounding workers and
each tool's threads together (`stepped_workers`, `tool_threads`, `processor_rate`).

The long passes (scanning, recognising faces, describing pictures) each have a share, and a share
nobody is using goes to whoever is using theirs, re-divided every few seconds from the queue as
plain arithmetic with no state. A per-type limit is a cap, never a reservation: the pool's worker
count still bounds the whole. Zero means stop (recognition outside its hours). A number somebody
typed is a limit, never raised or cut. Work a person waits on (a thumbnail) counts against every
share but is never capped, or it would sit behind a pass's thousands of queued probes.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping

#: How much of this device background work uses while somebody is at it, in percent: a quarter
#: leaves a person three quarters of the machine. The stored setting's default
#: (Settings > Performance).
STEP_BACK_SHARE = 25

#: The whole device, in the same percent. What every share is read against while nobody is here.
WHOLE_DEVICE = 100


def stepped_workers(full: int, percent: int) -> int:
    """How many workers run while `percent` of the device is in force, out of `full`.

    Rounded up, so a pool of three at a quarter keeps one worker rather than stopping, and never
    above `full`: a share is a ceiling, and a share of everything is the full count.
    """
    if full <= 1:
        return max(1, full)
    share = min(WHOLE_DEVICE, max(1, percent))
    return max(1, min(full, math.ceil(full * share / WHOLE_DEVICE)))


def tool_threads(cores: int, *, running: int, percent: int) -> int:
    """How many threads one background tool may use when `running` tools share `percent` of the
    device's `cores`.

    The share of the logical processors divided by how many tools run together, so the tools
    together never ask for more than the share; never below one, because a tool cannot run on
    none. With the whole device in force this is the cores divided by the workers.
    """
    share = min(WHOLE_DEVICE, max(1, percent))
    usable = max(1, cores * share // WHOLE_DEVICE)
    return max(1, usable // max(1, running))


def processor_rate(threads: int, cores: int) -> int:
    """`threads` of `cores` as the operating system's processor rate: hundredths of a percent of
    the whole machine, 1 to 10,000. What holds a tool to its threads when its own thread flags do
    not (a tool reading several inputs together runs a decoder for each)."""
    return max(1, min(10_000, threads * 10_000 // max(1, cores)))


def divide(
    *,
    workers: int,
    entitlements: Mapping[str, int],
    busy: Mapping[str, int],
    fixed: Collection[str] = frozenset(),
    waited_on: Collection[str] = frozenset(),
) -> dict[str, int]:
    """How many of each kind may run together, given who actually has work.

    `entitlements` is what each kind gets when everything competes (what the settings resolve to);
    `busy` is each kind's unfinished work. A `fixed` kind gets its own typed number whoever else has
    work; a share is divided against only the kinds with work, plus itself so nothing starves as
    its first job arrives, out of what the fixed kinds with work leave. `waited_on` kinds count
    against the shares and are left out of the answer. A claimant entitled to anything gets at
    least one, so a pass slows rather than stops.
    """
    if workers <= 0:
        return dict.fromkeys(entitlements, 0)

    wanting = {kind for kind, count in busy.items() if count > 0 and entitlements.get(kind, 0) > 0}
    limited = {kind for kind in fixed if kind in entitlements}
    # What the fixed kinds with work take before anything is divided. A fixed kind with nothing
    # to do takes nothing: it is not a reservation, and its number is back the moment work arrives.
    taken = sum(min(entitlements[kind], workers) for kind in wanting & limited)
    left = max(1, workers - taken)
    sharing = wanting - limited
    allowances: dict[str, int] = {}
    for kind, entitled in entitlements.items():
        if kind in waited_on:
            # Counted in `wanting` so the passes make room; never capped.
            continue
        if entitled <= 0:
            # Zero means stop: recognition outside its overnight hours. Not a proportion, and not
            # something a redistribution may quietly turn into "a little bit".
            allowances[kind] = 0
            continue
        if kind in limited:
            # A limit somebody typed. Not raised when the rest of the machine is idle and not cut
            # when it is busy, because the label says the number.
            allowances[kind] = min(entitled, workers)
            continue
        against = sum(entitlements[other] for other in sharing | {kind})
        allowances[kind] = max(1, min(left, left * entitled // against))
    return allowances


def unfinished_by_type(counts: Mapping[str, int], kinds: Mapping[str, int]) -> dict[str, int]:
    """The queue's per-type counts, filtered to the kinds this budget divides between.

    Its own function so the caller passes the queue's whole answer without having to know which
    types are in the budget, and so a kind that has never had a job reads as zero rather than as
    missing: the difference decides whether its share is up for redistribution.
    """
    return {kind: counts.get(kind, 0) for kind in kinds}
