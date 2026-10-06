# SPDX-License-Identifier: AGPL-3.0-or-later
"""Whether a kind of background work is wanted, and whether it can run at all.

Two questions about the same work, asked by different people and answered from different places,
which is why they are declared together and kept apart.

**Is it switched on** is a person's answer. It is a stored preference: the pictures, the faces,
the meaning and the marks are each gated by a key the composition root names (see the import gates
in the application's wiring), and the walk that finds files, the duplicate sweep and the folder pass
are gated here, without which there would be no way to say no to them at all.

**Can it run** is a fact about the machine. Recognition needs a runtime and a set of model files; a
description needs the same. Neither is a preference and neither is stored, so it is asked of the
feature that knows, every time it is wanted.

## Why the switch is declared per JOB TYPE and the readiness per FAMILY

Because that is the honest grain of each.

A switch stops work. "Stop scanning" cannot mean "stop probing a file that is already in the
library": probe and import are in the SCAN family too, and a file taken in with no probe has no
dimensions and cannot be drawn on a wall. So a switch names the job types it actually refuses:
the walk, the whole-library pass and the catch-up, and leaves the rest of the family alone. It is
the same grain `WorkAhead` registers at and the same grain the import gates use, so there is one
vocabulary for a kind of work rather than two.

Readiness is about a capability, and a capability belongs to a feature, and a feature is a family:
every job type under IDENTIFY needs the same runtime. Asked per type it would be the same answer
repeated, and the screen that draws it draws one row per family.

## Why the composition root fills both in

Neither answer is the queue's to know. A switch is a settings key, and the kernel must not learn
that settings exist in a particular shape; readiness lives inside the feature. So a caller hands in
a function for each, exactly as it does for the counters on `WorkAhead`, and this holds the result.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sift.kernel.jobs.families import Family
from sift.kernel.log import get_logger

log = get_logger(__name__)


class JobSwitchedOff(Exception):
    """This work is switched off, so nothing was queued. The message is meant to be read.

    Raised rather than returned, because the two callers want opposite things and only the caller
    knows which it is. Somebody pressing a button needs a sentence saying why nothing happened; a
    watcher noticing a file needs to carry on quietly, and catches this. A queue that silently
    dropped the work would leave the person with no answer at all, which is the worse of the two
    mistakes to make by default.
    """


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

#: When a family's quiet-hours window opens, as the "HH:MM" the person set, or None when it has
#: no window. See `Switchboard.declare_window`.
Opens = Callable[[], Awaitable[str | None]]


@dataclass(frozen=True, slots=True)
class QuietHold:
    """What quiet hours hold back right now: whether the range is open, and whose work waits for it.

    `types` are the job types whose task is set to "In quiet hours". Work of those types that nobody
    pressed is not handed out while the range is shut, which is how it waits for the opening and
    how it is paused at the close. A press is never held, whatever its type; a press of "At quiet
    hours" is held however its task is set. Both are marks on the ROW (`jobs.timing`), so this only
    ever speaks for work nobody asked for.
    """

    open: bool = True
    types: frozenset[str] = frozenset()


#: The answer a board gives with nothing declared: always open, nothing held.
NOTHING_HELD = QuietHold()

AskQuiet = Callable[[], Awaitable[QuietHold]]


class Switchboard:
    """What the application knows about each kind of background work that the queue cannot.

    Empty by default, and an empty board allows everything: a job type nothing has declared a
    switch for is always on, and a family nothing has been asked about is ready. That is what keeps
    this from becoming a list every new job has to be added to before it will run at all: the
    same rule the import gates follow.
    """

    def __init__(self) -> None:
        self._switches: dict[str, Switch] = {}
        self._asks: dict[Family, Ask] = {}
        self._windows: dict[Family, Opens] = {}
        self._quiet: AskQuiet | None = None
        self._asking: asyncio.Future[dict[Family, Readiness]] | None = None

    # --- the switch ----------------------------------------------------------------------

    def declare(self, switch: Switch, *job_types: str) -> None:
        """Say which setting switches these job types off, and what a refusal says.

        Several types share one switch on purpose: the walk, the whole-library pass and the
        catch-up are one thing to a person and three rows to the queue. Declaring the same type
        twice replaces the first, which is what makes this safe to call from a composition root
        that may build a feature more than once in a test.
        """
        for job_type in job_types:
            self._switches[job_type] = switch

    def switch_of(self, job_type: str) -> Switch | None:
        """This type's switch, or None for work nobody can turn off."""
        return self._switches.get(job_type)

    def switches(self) -> dict[str, Switch]:
        """Every declared switch by job type, copied."""
        return dict(self._switches)

    async def refusal(self, job_type: str, *, pressed: bool = False) -> str | None:
        """Why this work may not be queued, or None when it may.

        A PRESS IS NEVER REFUSED. Every switch declared here answers whether work starts ON ITS OWN
        (a task's When, read as "anything but Only when I press it"), and somebody pressing Run
        now, a queue's Scan now or a file's Run task has just answered that for themselves. Refusing
        them with the sentence for work nobody asked for would say "Scanning is switched off" to
        the person who had just pressed Scan.

        A switch that cannot be read is treated as ON. The alternative is a broken settings read
        silencing every background pass in the application at once, which is the one wrong answer
        that looks like nothing being wrong.
        """
        switch = self._switches.get(job_type)
        if switch is None or pressed:
            return None
        try:
            on = await switch.on()
        except Exception as exc:
            log.warning("switchboard.unreadable", job_type=job_type, key=switch.key, error=str(exc))
            return None
        return None if on else switch.refusal

    # --- whether it can run at all -------------------------------------------------------

    def declare_ready(self, family: Family, ask: Ask) -> None:
        """Say how to find out whether this family's work can run on this machine."""
        self._asks[family] = ask

    async def readiness(self) -> dict[Family, Readiness]:
        """Every family that was asked about, and what it said.

        A family whose answer cannot be read is LEFT OUT rather than reported as not ready, for the
        reason `WorkAhead` leaves a failed counter out: "not known" is the honest answer to a read
        that did not come back, and a screen that said "waiting for the runtime" over an
        unreachable endpoint would be inventing a fault. Callers arriving while one ask is out
        share its answer: every worker claims at once, and each would ask every feature again.
        """
        if self._asking is None or self._asking.get_loop() is not asyncio.get_running_loop():
            self._asking = asyncio.ensure_future(self._ask_every_family())
            self._asking.add_done_callback(self._asked)
        return dict(await asyncio.shield(self._asking))

    def _asked(self, done: asyncio.Future[dict[Family, Readiness]]) -> None:
        if self._asking is done:
            self._asking = None

    async def _ask_every_family(self) -> dict[Family, Readiness]:
        answers: dict[Family, Readiness] = {}
        for family, ask in self._asks.items():
            try:
                answers[family] = await ask()
            except Exception as exc:
                log.warning("switchboard.readiness_failed", family=family.value, error=str(exc))
        return answers

    # --- when it is allowed to run -------------------------------------------------------

    def declare_window(self, family: Family, opens: Opens) -> None:
        """Say when this family's overnight window opens, so a pass held by it can say when.

        The window itself is the pool's caps going to nought outside the hours, which the Activity
        screen reads as "held". What the caps cannot say is WHEN they come back, and "Waiting for
        tonight's window." without the hour leaves somebody guessing whether that is ten minutes or
        ten hours. The hour is a setting the feature owns, so (like the switch and the readiness
        above) the composition root hands in the function that reads it.
        """
        self._windows[family] = opens

    def declare_quiet_hours(self, ask: AskQuiet) -> None:
        """Say how to find out whether quiet hours are open and whose automatic work waits for them.

        Handed in for the reason the switch and the readiness are: the range and each task's When
        are settings, and the queue must not learn that settings exist. One answer for the whole
        install, because quiet hours is one range.
        """
        self._quiet = ask

    async def quiet_hold(self) -> QuietHold:
        """Whether quiet hours are open and which types wait for them. Nothing held if undeclared.

        A read that fails holds NOTHING back, for the reason a switch that cannot be read is on: a
        broken settings read must not silently stop every quiet-hours task in the install. The
        worse error would look like nothing being wrong.
        """
        if self._quiet is None:
            return NOTHING_HELD
        try:
            return await self._quiet()
        except Exception as exc:
            log.warning("switchboard.quiet_unreadable", error=str(exc))
            return NOTHING_HELD

    async def window_opens(self, family: Family) -> str | None:
        """When this family's window opens, or None: never declared, or not readable right now.

        A read that fails is None rather than an error, for the reason `readiness` leaves a failed
        answer out: the sentence falls back to saying there is a window, which is true, instead of
        inventing an hour.
        """
        opens = self._windows.get(family)
        if opens is None:
            return None
        try:
            answer = await opens()
        except Exception as exc:
            log.warning("switchboard.window_unreadable", family=family.value, error=str(exc))
            return None
        return (answer or "").strip() or None
