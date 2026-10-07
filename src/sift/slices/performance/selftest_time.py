# SPDX-License-Identifier: AGPL-3.0-or-later
"""How many rounds a run here can publish, and how long the run going has left by its stages."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from itertools import combinations

from sift.slices.performance.budget import GRACE_SECONDS, Budget
from sift.slices.performance.selftest import LEVELS, planned_levels


def planned_rounds(
    cores: int, levels: Sequence[int] = LEVELS, *, with_midpoint: bool = True
) -> int:
    """Every round a run here can publish: the doublings, and the one step back between two."""
    planned = planned_levels(cores, levels)
    halves = {(low + high) // 2 for low, high in combinations(planned, 2) if (low + high) % 2 == 0}
    return len(planned) + (1 if with_midpoint and halves - set(planned) else 0)


def stage_lengths(budget: Budget, *, kind: str) -> dict[str, float]:
    """How long each stage begun took, under `kind`'s keys: to the next one's start, the last to now."""
    if not budget.stages:
        return {}
    ends = [*(one.began for one in budget.stages[1:]), budget.clock()]
    return {
        f"{kind}.{one.name}": round(end - one.began, 1)
        for one, end in zip(budget.stages, ends, strict=True)
    }


def seconds_left(budget: Budget, typical: Mapping[str, float], *, kind: str) -> float | None:
    """The run's time left: the stage going and every stage to come at its `typical` length here,
    never past the run's end. Without a kept length, the end itself; None for a run with no end."""
    now = budget.clock()
    start = now if budget.started is None else budget.started
    bound = max(0.0, start + budget.seconds - GRACE_SECONDS - now)
    prefix = f"{kind}."
    known = {name[len(prefix) :]: at for name, at in typical.items() if name.startswith(prefix)}
    if not any(name in known for name in budget.shares):
        return None if math.isinf(bound) else bound
    begun = {one.name for one in budget.stages}
    going = budget.stages[-1] if budget.stages else None
    left = sum(known.get(name, 0.0) for name in budget.shares if name not in begun)
    if going is not None:
        expected = going.began + known.get(going.name, going.at - going.began)
        left += max(0.0, min(going.at, expected) - now)
    return min(bound, left)
