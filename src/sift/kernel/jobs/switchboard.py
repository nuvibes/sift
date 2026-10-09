# SPDX-License-Identifier: AGPL-3.0-or-later
"""Whether a kind of background work is switched on, and whether this machine can run it.

A switch is per job type and readiness per family; the composition root fills both in."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from sift.kernel.jobs.families import Family
from sift.kernel.log import get_logger

log = get_logger(__name__)


class JobSwitchedOff(Exception):
    """This work is switched off, so nothing was queued. The message is meant to be read."""


@dataclass(frozen=True, slots=True)
class Switch:
    """One kind of work's off switch: which setting holds it, and what a refusal says."""

    key: str
    """The settings key. On the wire so the screen can draw the control from the declaration
    instead of keeping its own list of which work has a switch."""
    refusal: str
    """The sentence somebody reads when they ask for work that is switched off. Written where the
    switch is declared, so the words and the setting cannot drift apart."""
    on: Callable[[], Awaitable[bool]]
    """Whether it is switched on right now. Read per request, never held, so a change takes effect
    on the next job rather than the next restart."""


@dataclass(frozen=True, slots=True)
class Readiness:
    """Whether a family's work can run on this machine, and the reason it cannot."""

    ready: bool
    problem: str | None = None
    """In the feature's own words, or None. A family that is simply switched off has no problem to
    report: that is the switch's answer, not this one."""


Ask = Callable[[], Awaitable[Readiness]]

#: When a family's quiet-hours window opens, as "HH:MM", or None without one.
Opens = Callable[[], Awaitable[str | None]]


@dataclass(frozen=True, slots=True)
class QuietHold:
    """What quiet hours hold back now: whether the range is open, and whose unpressed work waits."""

    open: bool = True
    types: frozenset[str] = frozenset()


NOTHING_HELD = QuietHold()

AskQuiet = Callable[[], Awaitable[QuietHold]]


@dataclass(slots=True)
class OneReading:
    """The app settings one question read, read once and held for that question alone."""

    values: dict[str, str] | None = None


_READING: ContextVar[OneReading | None] = ContextVar("switchboard_reading", default=None)


def one_reading_now() -> OneReading | None:
    """The reading of the question in progress, or None outside one."""
    return _READING.get()


@asynccontextmanager
async def one_reading() -> AsyncIterator[None]:
    """Every setting a question asks, from one read; a nested question shares the outer's."""
    if _READING.get() is not None:
        yield
        return
    token = _READING.set(OneReading())
    try:
        yield
    finally:
        _READING.reset(token)


class Switchboard:
    """What the app knows about each kind of background work; an empty board allows everything."""

    def __init__(self) -> None:
        self._switches: dict[str, Switch] = {}
        self._shown: dict[str, Callable[[], Awaitable[bool]]] = {}
        self._asks: dict[Family, Ask] = {}
        self._windows: dict[Family, Opens] = {}
        self._quiet: AskQuiet | None = None
        self._asking: asyncio.Future[dict[Family, Readiness]] | None = None

    def declare(self, switch: Switch, *job_types: str) -> None:
        """Say which setting switches these job types off; declaring a type again replaces it."""
        for job_type in job_types:
            self._switches[job_type] = switch

    def declare_shown(self, on: Callable[[], Awaitable[bool]], *job_types: str) -> None:
        """Say how Activity reads whether these types start on their own."""
        for job_type in job_types:
            self._shown[job_type] = on

    async def shown_off(self, job_type: str) -> bool:
        """Whether Activity reads this type as switched off; unreadable reads as on."""
        if await self.refusal(job_type) is not None:
            return True
        ask = self._shown.get(job_type)
        if ask is None:
            return False
        try:
            async with one_reading():
                return not await ask()
        except Exception as exc:
            log.warning("switchboard.unreadable", job_type=job_type, error=str(exc))
            return False

    def switch_of(self, job_type: str) -> Switch | None:
        """This type's switch, or None for work nobody can turn off."""
        return self._switches.get(job_type)

    def switches(self) -> dict[str, Switch]:
        """Every declared switch by job type, copied."""
        return dict(self._switches)

    async def refusal(self, job_type: str, *, pressed: bool = False) -> str | None:
        """Why this work may not be queued, or None. A press is never refused."""
        # An unreadable switch is on: a broken read must not silence every background pass.
        switch = self._switches.get(job_type)
        if switch is None or pressed:
            return None
        try:
            async with one_reading():
                on = await switch.on()
        except Exception as exc:
            log.warning("switchboard.unreadable", job_type=job_type, key=switch.key, error=str(exc))
            return None
        return None if on else switch.refusal

    def declare_ready(self, family: Family, ask: Ask) -> None:
        """Say how to find out whether this family's work can run on this machine."""
        self._asks[family] = ask

    async def readiness(self) -> dict[Family, Readiness]:
        """Every family asked about and its answer; a failed read is left out, never "not ready"."""
        if self._asking is None or self._asking.get_loop() is not asyncio.get_running_loop():
            self._asking = asyncio.ensure_future(self._ask_every_family())
            self._asking.add_done_callback(self._asked)
        return dict(await asyncio.shield(self._asking))

    def _asked(self, done: asyncio.Future[dict[Family, Readiness]]) -> None:
        if self._asking is done:
            self._asking = None

    async def _ask_every_family(self) -> dict[Family, Readiness]:
        answers: dict[Family, Readiness] = {}
        async with one_reading():
            for family, ask in self._asks.items():
                try:
                    answers[family] = await ask()
                except Exception as exc:
                    log.warning("switchboard.readiness_failed", family=family.value, error=str(exc))
        return answers

    def declare_window(self, family: Family, opens: Opens) -> None:
        """Say when this family's overnight window opens, so a held pass can say when."""
        self._windows[family] = opens

    def declare_quiet_hours(self, ask: AskQuiet) -> None:
        """Say how to read quiet hours: whether open and whose automatic work waits for them."""
        self._quiet = ask

    async def quiet_hold(self) -> QuietHold:
        """Whether quiet hours are open and which types wait; a failed read holds nothing back."""
        if self._quiet is None:
            return NOTHING_HELD
        try:
            async with one_reading():
                return await self._quiet()
        except Exception as exc:
            log.warning("switchboard.quiet_unreadable", error=str(exc))
            return NOTHING_HELD

    async def window_opens(self, family: Family) -> str | None:
        """When this family's window opens, or None when undeclared or unreadable."""
        opens = self._windows.get(family)
        if opens is None:
            return None
        try:
            async with one_reading():
                answer = await opens()
        except Exception as exc:
            log.warning("switchboard.window_unreadable", family=family.value, error=str(exc))
            return None
        return (answer or "").strip() or None
