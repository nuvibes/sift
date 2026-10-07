# SPDX-License-Identifier: AGPL-3.0-or-later
"""Costly answers kept while they can still be right.

`MarkedMemo` keeps an answer under the change bus's mark, which moves on every announcement, and
is bounded by count: the mark decides staleness. `PacedAnswer` keeps one no announcement covers.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from time import monotonic

from sift.kernel.jobs.work_ahead import COUNTING_SHARE, FRESH_FOR_SECONDS

#: How many answers to keep. A filter panel asks about a dozen dimensions per screen, and the
#: number of distinct screens anybody has open together is small; this is generous.
DEFAULT_KEPT = 256


class MarkedMemo[T]:
    """Answers kept under the mark they were computed at."""

    def __init__(self, *, kept: int = DEFAULT_KEPT) -> None:
        self._kept = kept
        self._answers: OrderedDict[tuple[Hashable, str], T] = OrderedDict()

    async def get(self, key: Hashable, mark: str | None, compute: Callable[[], Awaitable[T]]) -> T:
        """The answer for `key` as of `mark`, computing it if it is not held.

        No mark means nothing is announcing, so nothing could ever say the answer went stale: the
        answer is computed and not kept.
        """
        if mark is None:
            return await compute()
        held = self._answers.get((key, mark))
        if held is not None:
            self._answers.move_to_end((key, mark))
            return held
        answer = await compute()
        self._answers[(key, mark)] = answer
        while len(self._answers) > self._kept:
            self._answers.popitem(last=False)
        return answer

    def forget(self) -> None:
        self._answers.clear()


class PacedAnswer[T]:
    """Kept for `fresh_for` seconds and ten times what it last cost, as Activity's counts are: a
    file being read announces nothing."""

    def __init__(
        self,
        compute: Callable[[], Awaitable[T]],
        *,
        fresh_for: float = FRESH_FOR_SECONDS,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._compute = compute
        self._fresh_for = fresh_for
        self._clock = clock
        self._held: tuple[float, float, T] | None = None
        self._asking = asyncio.Lock()

    async def get(self) -> T:
        """The answer held, or a fresh one; a second asker waits for the one count under way."""
        async with self._asking:
            if self._held is not None:
                at, cost, answer = self._held
                if self._clock() - at < max(self._fresh_for, cost / COUNTING_SHARE):
                    return answer
            started = self._clock()
            answer = await self._compute()
            ended = self._clock()
            self._held = (ended, ended - started, answer)
            return answer
