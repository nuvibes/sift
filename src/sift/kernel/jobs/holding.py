# SPDX-License-Identifier: AGPL-3.0-or-later
"""What somebody paused: some job types, or the whole queue.

Held by the worker pool and never written down, so a restart starts everything again: a pause
means "not now", and one kept across a restart would stop the library's work with nothing on
screen still saying why. A paused type is claimed under a cap of zero, which the claim already
reads as paused, so the queue's rows never change and Resume is exact.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping


class Holding:
    """The pool's pause: which types are held, whether everything is, and the caps that follow."""

    def __init__(self, on_release: Callable[[], None] = lambda: None) -> None:
        self.held: frozenset[str] = frozenset()
        #: Products held: a step that makes only these is passed over, whatever its type, so a
        #: pass whose files run as another pass's steps pauses and that pass runs on.
        self.products: frozenset[str] = frozenset()
        self.held_all = False
        self._on_release = on_release

    def hold(
        self, job_types: Collection[str] | None = None, products: Collection[str] = ()
    ) -> None:
        """Pause these types and products, or every type with none named."""
        if job_types is None:
            self.held_all = True
        else:
            self.held = self.held | frozenset(job_types)
            self.products = self.products | frozenset(products)

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
        self._on_release()

    def caps(self, limits: Mapping[str, int]) -> Mapping[str, int]:
        """The caps a claim is handed: `limits`, with every held type at zero."""
        return {**limits, **dict.fromkeys(self.held, 0)} if self.held else limits
