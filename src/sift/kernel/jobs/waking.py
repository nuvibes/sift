# SPDX-License-Identifier: AGPL-3.0-or-later
"""What wakes the worker pool before its interval is up: a press, and work arriving."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Sequence

#: Subscribes a listener and hands back how to let it go.
Listen = Callable[[Callable[[], None]], Callable[[], None]]


class Waking:
    """The pool's two early wakes, and the sources it hears them from while it runs."""

    def __init__(self, woken_by: Sequence[Listen] = ()) -> None:
        #: Set by `wake`: the supervisor reconfigures now rather than at the end of its interval.
        self.reconfigure = asyncio.Event()
        #: Replaced on every `work_arrived`, so each idle worker waiting on the old one wakes once.
        self.arrived = asyncio.Event()
        self._woken_by = tuple(woken_by)
        self._unwake: list[Callable[[], None]] = []

    def listen(self, for_work: Listen) -> None:
        self._unwake = [listen(self.wake) for listen in self._woken_by]
        self._unwake.append(for_work(self.work_arrived))

    def stop_listening(self) -> None:
        for unwake in self._unwake:
            unwake()
        self._unwake = []

    def wake(self) -> None:
        """Reconfigure now: a press for turbo mode or eco mode is taken within a moment.

        Called on the event loop; a wake while one is pending is the same wake.
        """
        self.reconfigure.set()

    def work_arrived(self) -> None:
        """Every idle worker claims now rather than at the end of its poll: a task was started."""
        arrived, self.arrived = self.arrived, asyncio.Event()
        arrived.set()


async def first_of(events: Iterable[asyncio.Event], within: float) -> None:
    """Wait until any of these is set, or `within` seconds pass."""
    waits = [asyncio.ensure_future(event.wait()) for event in events]
    try:
        await asyncio.wait(waits, timeout=within, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for wait in waits:
            wait.cancel()
        await asyncio.gather(*waits, return_exceptions=True)
