# SPDX-License-Identifier: AGPL-3.0-or-later
"""What wakes the worker pool before its interval is up: a press, and work arriving."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Sequence

#: Subscribes a listener and hands back how to let it go.
Listen = Callable[[Callable[[], None]], Callable[[], None]]


#: Wakes kept for workers about to go idle, so work queued between a worker's empty claim and its
#: wait is not left to the long wait. Few: a busy worker claims anyway when its job ends.
WAKES_KEPT = 4

#: The longest an idle worker waits with nothing said: what a missed wake costs, never a stall.
LONGEST_IDLE_SECONDS = 120.0


class Waking:
    """The pool's early wakes, and the sources it hears them from while it runs."""

    def __init__(self, woken_by: Sequence[Listen] = ()) -> None:
        #: Set by `wake`: the supervisor reconfigures now rather than at the end of its interval.
        self.reconfigure = asyncio.Event()
        #: One per idle worker, oldest first: `work_arrived` sets one, `wake_all` every one.
        self._idle: list[asyncio.Event] = []
        self._kept = 0
        #: Set by `retime`: every idle worker reads the next due moment again, claiming nothing.
        self._retimed = asyncio.Event()
        self._woken_by = tuple(woken_by)
        self._unwake: list[Callable[[], None]] = []

    def listen(self, for_work: Listen, for_anything: Listen, for_retime: Listen) -> None:
        self._unwake = [listen(self.wake) for listen in self._woken_by]
        self._unwake.append(for_work(self.work_arrived))
        self._unwake.append(for_anything(self.wake_all))
        self._unwake.append(for_retime(self.retime))

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
        """One job a worker could take now: one idle worker claims, not every one."""
        if self._idle:
            self._idle.pop(0).set()
        else:
            self._kept = min(self._kept + 1, WAKES_KEPT)

    def wake_all(self) -> None:
        """Work of no known kind may be claimable (a resume, a retry, a cap raised): every idle
        worker claims."""
        for idle in self._idle:
            idle.set()
        self._idle = []
        self._kept = WAKES_KEPT

    def retime(self) -> None:
        """A row was put off to a moment of its own: the idle workers' waits are measured again."""
        retimed, self._retimed = self._retimed, asyncio.Event()
        retimed.set()

    @property
    def idle_workers(self) -> int:
        return len(self._idle)

    async def idle(self, stops: Iterable[asyncio.Event], within: float) -> bool:
        """Wait for a wake, a stop, or `within` seconds; at once for a wake kept meanwhile. False
        when only a `retime` ended it: measure the wait again rather than claim."""
        if self._kept:
            self._kept -= 1
            return True
        woken, retimed = asyncio.Event(), self._retimed
        self._idle.append(woken)
        try:
            await first_of((*stops, woken, retimed), within)
        finally:
            if woken in self._idle:
                self._idle.remove(woken)
        return woken.is_set() or not retimed.is_set()


async def first_of(events: Iterable[asyncio.Event], within: float) -> None:
    """Wait until any of these is set, or `within` seconds pass."""
    waits = [asyncio.ensure_future(event.wait()) for event in events]
    try:
        await asyncio.wait(waits, timeout=within, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for wait in waits:
            wait.cancel()
        await asyncio.gather(*waits, return_exceptions=True)
