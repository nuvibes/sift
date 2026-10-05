# SPDX-License-Identifier: AGPL-3.0-or-later
"""One wall clock for a benchmark run, shared among its stages, so a slow device measures less and
never longer. A stage's deadline is its weight's part of the time left, so what one leaves unused
passes to the ones after it."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

#: The whole run, the drain before it included.
WHOLE_SECONDS = 300.0

#: A first folder's quick part, said on screen as about a minute.
FIRST_PART_SECONDS = 75.0

#: How long past a stage's deadline the backstop waits, so the stage's own deadline ends a take
#: first and keeps what it measured.
GRACE_SECONDS = 1.0

ENCODING = "encoding"
DECODER = "decoder"
STORAGE = "storage"
PREVIEWS = "previews"
MODELS = "models"
TOGETHER = "together"

#: Each stage's part of a whole run, in seconds of the 300; sized from a busy device's takes.
WHOLE_SHARES: Mapping[str, float] = {
    ENCODING: 60,
    DECODER: 10,
    STORAGE: 45,
    PREVIEWS: 45,
    MODELS: 90,
    TOGETHER: 50,
}
FIRST_PART_SHARES: Mapping[str, float] = {ENCODING: 45, DECODER: 5, STORAGE: 25}


@dataclass
class Deadline:
    """A stage's end on the run's clock; past it the stage keeps what it has and stops."""

    name: str
    at: float = math.inf
    clock: Callable[[], float] = time.monotonic
    cut: bool = False

    def passed(self) -> bool:
        if self.clock() >= self.at:
            self.cut = True
        return self.cut

    def part(self, of: int) -> Deadline:
        """An equal part of what's left, for one of `of` things the stage still has to do."""
        now = self.clock()
        return Deadline(self.name, now + max(0.0, self.at - now) / max(1, of), self.clock)

    async def within[T](self, step: Awaitable[T], *, grace: float = 0.0) -> T | None:
        """`step`, ended at the deadline: a cancel ends the tools it started. None when cut."""
        if self.passed():
            if asyncio.iscoroutine(step):
                step.close()
            return None
        when = (
            None
            if math.isinf(self.at)
            else asyncio.get_running_loop().time() + (self.at + grace - self.clock())
        )
        timeout = asyncio.timeout_at(when)
        try:
            async with timeout:
                return await step
        except TimeoutError:
            if not timeout.expired():
                raise
            self.cut = True
            return None


@dataclass
class Budget:
    """The run's clock and the stages' weights; `stage` hands each its deadline as it begins."""

    seconds: float = math.inf
    shares: Mapping[str, float] = field(default_factory=dict)
    started: float | None = None
    clock: Callable[[], float] = time.monotonic
    stages: list[Deadline] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._left = dict(self.shares)
        if self.started is None:
            self.started = self.clock()

    def stage(self, name: str) -> Deadline:
        now = self.clock()
        weight = self._left.pop(name, 0.0)
        end = (self.started or now) + self.seconds - GRACE_SECONDS
        if math.isinf(end):
            deadline = Deadline(name, math.inf, self.clock)
        else:
            weights = weight + sum(self._left.values())
            share = max(0.0, end - now) * (weight / weights if weights else 1.0)
            deadline = Deadline(name, now + share, self.clock)
        self.stages.append(deadline)
        return deadline

    def cut(self) -> list[str]:
        """The stages that reached their deadline, in the order they ran, each once."""
        return list(dict.fromkeys(one.name for one in self.stages if one.cut))
