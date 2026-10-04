# SPDX-License-Identifier: AGPL-3.0-or-later
"""An answer kept until the library moves.

Some reads cost what the whole visible library costs and are asked again on every keystroke: the
counts under the filter panel are the case. Keeping the last answer is right exactly as long as
nothing has changed, and the change bus already says when something has: its mark moves on every
announcement. So an answer is kept under the mark it was computed at, and the next asker under the
same mark gets it back; a different mark means a different library and the answer is thrown away.

Bounded, and by count rather than by time. A time is a guess about how stale is acceptable; a
count is a limit on memory, and the mark is what decides staleness.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable

#: How many answers to keep. A filter panel asks about a dozen dimensions per screen, and the
#: number of distinct screens anybody has open at once is small; this is generous.
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
