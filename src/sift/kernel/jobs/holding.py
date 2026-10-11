# SPDX-License-Identifier: AGPL-3.0-or-later
"""What somebody paused: some job types, or the whole queue.

Held by the worker pool and kept in the queue's `job_holds`, so a restart starts as paused as it
stopped and the Tasks screen still says Paused. A paused type is claimed under a cap of zero, which
the claim already reads as paused, so the queue's rows never change and Resume is exact.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass

from sift.kernel.log import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Pause:
    """What is held: the whole queue, and the types and products held one by one."""

    everything: bool = False
    types: frozenset[str] = frozenset()
    products: frozenset[str] = frozenset()


class Holding:
    """The pool's pause: which types are held, whether everything is, and the caps that follow.

    `keep` writes each change through, in the order they were pressed; `kept` waits for them."""

    def __init__(
        self, on_release: Callable[[], None], keep: Callable[[Pause], Awaitable[None]]
    ) -> None:
        self.held: frozenset[str] = frozenset()
        #: Products held: a step that makes only these is passed over, whatever its type, so a
        #: pass whose files run as another pass's steps pauses and that pass runs on.
        self.products: frozenset[str] = frozenset()
        self.held_all = False
        self._on_release = on_release
        self._keep = keep
        self._writing: asyncio.Task[None] | None = None

    @property
    def pause(self) -> Pause:
        return Pause(self.held_all, self.held, self.products)

    def restore(self, pause: Pause) -> None:
        """Hold what was kept, before any worker claims."""
        self.held_all = pause.everything
        self.held = pause.types
        self.products = pause.products

    def hold(
        self, job_types: Collection[str] | None = None, products: Collection[str] = ()
    ) -> None:
        """Pause these types and products, or every type with none named."""
        if job_types is None:
            self.held_all = True
        else:
            self.held = self.held | frozenset(job_types)
            self.products = self.products | frozenset(products)
        self._changed()

    def release(
        self, job_types: Collection[str] | None = None, products: Collection[str] = ()
    ) -> None:
        """Resume these types and products, or the whole queue with none named, and wake the
        idle workers."""
        if job_types is None:
            self.held_all = False
        else:
            self.held = self.held - frozenset(job_types)
            self.products = self.products - frozenset(products)
        self._changed()
        self._on_release()

    def caps(self, limits: Mapping[str, int]) -> Mapping[str, int]:
        """The caps a claim is handed: `limits`, with every held type at zero."""
        return {**limits, **dict.fromkeys(self.held, 0)} if self.held else limits

    def _changed(self) -> None:
        self._writing = asyncio.get_running_loop().create_task(self._write(self._writing))

    async def _write(self, before: asyncio.Task[None] | None) -> None:
        # After the write before it, and of the pause as it is by then: the last press lands last.
        if before is not None:
            await before
        try:
            await self._keep(self.pause)
        except Exception:
            log.exception("jobs.pause_not_kept", everything=self.held_all)

    async def kept(self) -> None:
        """Wait until every change so far is written."""
        if self._writing is not None:
            await self._writing
