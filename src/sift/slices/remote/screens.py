# SPDX-License-Identifier: AGPL-3.0-or-later
"""The screens a user has offered to their phone, held in memory for as long as they keep saying so.

A screen is an open player or a Theater wall whose tab has offered itself. It says where it stands
(playing or not, where, how loud, what it can be asked to do) whenever that changes and every few
seconds while nothing does, and it is listed for as long as it keeps saying so. **Nothing about it
is stored**: what is playing is a fact about a tab, it cannot survive the tab, and a restart that
forgot every screen is correct, because every tab still open reports again within seconds.

Keyed by user AND screen, so a screen id that happens to match somebody else's names nothing of
theirs: another user's screen is exactly as absent as a screen that was never offered.

Access is from the event loop, which runs one thing at a time, so the plain dictionaries need no
lock: there is no await between reading them and writing them.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from sift.kernel.changes import RemoteAction
from sift.kernel.wiring import Part
from sift.slices.remote import tuning

Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class Extras:
    """What a screen offers beyond playing: its drawer's settings and the lists it chooses from.

    For a wall these are the cell being talked to. Each list is what a command's `value` counts
    into, so the phone never names a size, a layout or a preset the screen did not say it has.
    """

    repeat: str | None = None
    shuffle: bool | None = None
    loop_marks: int = 0
    qualities: tuple[str, ...] = ()
    quality: int | None = None
    favorite: bool | None = None
    count: int | None = None
    timer: int | None = None
    every_cell: bool = False
    cell_held: bool | None = None
    cell_muted: bool | None = None
    layouts: tuple[str, ...] = ()
    layout: int | None = None
    presets: tuple[str, ...] = ()
    cell_files: tuple[str | None, ...] = ()


NO_EXTRAS = Extras()


@dataclass(frozen=True, slots=True)
class Report:
    """What a screen said about itself, the last time it spoke."""

    label: str
    surface: str
    app: bool
    playing: bool
    position: float
    length: float | None
    file: str | None
    volume: int
    muted: bool
    supports: frozenset[RemoteAction]
    acted_on: str | None
    cells: int
    focused: int | None
    #: What the screen's drawer stands at, where it has one, so the phone draws each control lit
    #: or not exactly as the desk does. Defaults for a screen that says nothing about them.
    extras: Extras = NO_EXTRAS


@dataclass(slots=True)
class Screen:
    """One offered screen: whose it is, what it last said, and when."""

    user_id: str
    screen_id: str
    report: Report
    #: When it last spoke, on this process's monotonic clock. Never the wall clock, which a
    #: machine can step backwards.
    heard_at: float

    def position_at(self, now: float) -> float:
        """Where it has got to by now: where it said it was, carried on by the time since.

        Worked out here rather than left to the phone's clock, because the only clock both ends
        agree on is this one. The phone carries it on from the moment it read the list.
        """
        report = self.report
        if not report.playing:
            return report.position
        carried = report.position + max(0.0, now - self.heard_at)
        return carried if report.length is None else min(carried, report.length)


#: Which actions each surface may offer. A player offering a wall's verb, or the other way round,
#: is a report from something that is not Sift's own client, and is refused rather than listed.
SURFACES: dict[str, frozenset[RemoteAction]] = {
    "player": frozenset(action for action in RemoteAction if action.startswith("player.")),
    "theater": frozenset(action for action in RemoteAction if action.startswith("theater.")),
}


@dataclass(frozen=True, slots=True)
class Takes:
    """The value one action takes: its range, and whether it must be a whole number."""

    low: float
    high: float
    whole: bool = True


_STATE = Takes(0, 1)
_STEP = Takes(1, tuning.LONGEST_STEP_SECONDS, whole=False)
#: The repeat press's three answers, by their place in the one order it cycles through.
_REPEAT = Takes(0, 2)
#: A place in a list the screen reported. Held to that list's own length in `refusal`.
_CHOICE = Takes(0, tuning.MOST_CHOICES - 1)

#: What each action's `value` must be, or None for an action that takes none.
#:
#: Every number that arrives from a phone is bounded before it goes anywhere near a player, and the
#: bounds are here, once, rather than trusted to the desktop: a desktop is the thing being
#: controlled, and it should never be handed a volume of a million or a seek to minus an hour.
VALUES: dict[RemoteAction, Takes | None] = {
    RemoteAction.PLAY_PAUSE: _STATE,
    RemoteAction.BACK: _STEP,
    RemoteAction.FORWARD: _STEP,
    RemoteAction.SEEK_TO: Takes(0, tuning.LONGEST_SECONDS, whole=False),
    RemoteAction.PREVIOUS: None,
    RemoteAction.NEXT: None,
    RemoteAction.VOLUME_TO: Takes(0, 100),
    RemoteAction.MUTE: _STATE,
    RemoteAction.FILL: _STATE,
    RemoteAction.FAVORITE: _STATE,
    RemoteAction.COUNT: None,
    RemoteAction.THEATER_PAUSE_ALL: _STATE,
    RemoteAction.THEATER_MUTE_ALL: _STATE,
    RemoteAction.THEATER_CELL: Takes(0, tuning.MOST_CELLS - 1),
    RemoteAction.THEATER_PREVIOUS: None,
    RemoteAction.THEATER_NEXT: None,
    RemoteAction.REPEAT: _REPEAT,
    RemoteAction.SHUFFLE: _STATE,
    RemoteAction.LOOP: None,
    RemoteAction.SAVE_LOOP: None,
    RemoteAction.CLIP: _STEP,
    RemoteAction.RANDOM: None,
    RemoteAction.QUALITY: _CHOICE,
    RemoteAction.CORNER: None,
    RemoteAction.THEATER_PAUSE: _STATE,
    RemoteAction.THEATER_MUTE: _STATE,
    RemoteAction.THEATER_BACK: _STEP,
    RemoteAction.THEATER_FORWARD: _STEP,
    RemoteAction.THEATER_SEEK_TO: Takes(0, tuning.LONGEST_SECONDS, whole=False),
    RemoteAction.THEATER_VOLUME_TO: Takes(0, 100),
    RemoteAction.THEATER_REPEAT: _REPEAT,
    RemoteAction.THEATER_SHUFFLE: _STATE,
    RemoteAction.THEATER_LOOP: None,
    RemoteAction.THEATER_SAVE_LOOP: None,
    RemoteAction.THEATER_RANDOM: None,
    RemoteAction.THEATER_QUALITY: _CHOICE,
    RemoteAction.THEATER_SOLO: None,
    RemoteAction.THEATER_TIMER: Takes(0, tuning.LONGEST_TIMER_SECONDS),
    RemoteAction.THEATER_EVERY_CELL: None,
    RemoteAction.THEATER_LAYOUT: _CHOICE,
    RemoteAction.THEATER_PRESET: _CHOICE,
    RemoteAction.THEATER_CORNER: None,
}

#: The two scrubbers, each held to the length the screen reported.
_SEEKS = frozenset({RemoteAction.SEEK_TO, RemoteAction.THEATER_SEEK_TO})

#: Which list a choosing action counts into, so its value is held to the length the screen itself
#: reported: a phone showing an old list is refused rather than handed the wrong size or layout.
_CHOOSES_FROM: dict[RemoteAction, Callable[[Extras], tuple[str, ...]]] = {
    RemoteAction.QUALITY: lambda extras: extras.qualities,
    RemoteAction.THEATER_QUALITY: lambda extras: extras.qualities,
    RemoteAction.THEATER_LAYOUT: lambda extras: extras.layouts,
    RemoteAction.THEATER_PRESET: lambda extras: extras.presets,
}


def refusal(action: RemoteAction, value: float | None, screen: Screen) -> str | None:
    """Why this value cannot be sent to this screen, or None when it can.

    Some bounds are the SCREEN's rather than fixed: a seek cannot pass the length the screen
    reported, a cell must be one the wall reported having, and a size, a layout or a preset must be
    a place in the list the screen reported. Held to what the screen said rather
    than to a guess, so a phone showing an old list is refused rather than obeyed.
    """
    takes = VALUES[action]
    if takes is None:
        return None if value is None else "That takes no value."
    if value is None or not math.isfinite(value):
        return "That needs a value."
    if takes.whole and value != int(value):
        return "That needs a whole number."
    high = takes.high
    if action in _SEEKS and screen.report.length is not None:
        high = min(high, screen.report.length)
    if action is RemoteAction.THEATER_CELL:
        high = min(high, screen.report.cells - 1)
    chooses = _CHOOSES_FROM.get(action)
    if chooses is not None:
        high = min(high, len(chooses(screen.report.extras)) - 1)
    if not takes.low <= value <= high:
        return "That is out of range."
    return None


@dataclass(slots=True)
class _Bucket:
    tokens: float
    at: float


@dataclass(slots=True)
class Allowance:
    """How much one user may do, refilled at a steady rate: a token bucket per user.

    In memory, like everything here. A restart hands everybody a full allowance, which at worst is
    one burst, and is not worth a database write per press of a button.
    """

    burst: int
    per_second: float
    clock: Clock = time.monotonic
    _held: dict[str, _Bucket] = field(default_factory=dict)

    def spend(self, user_id: str) -> float:
        """Take one from this user's allowance: 0 when there was one, else the seconds to wait."""
        now = self.clock()
        bucket = self._held.get(user_id)
        if bucket is None:
            bucket = self._held[user_id] = _Bucket(float(self.burst), now)
        bucket.tokens = min(float(self.burst), bucket.tokens + (now - bucket.at) * self.per_second)
        bucket.at = now
        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return 0.0
        return (1.0 - bucket.tokens) / self.per_second


@dataclass(frozen=True, slots=True)
class Hold:
    """A phone driving one screen: what the desk calls it, and when it last said so."""

    label: str
    #: On this process's monotonic clock, like `Screen.heard_at`.
    at: float


class Screens:
    """Every offered screen in this process, by user."""

    def __init__(self, clock: Clock = time.monotonic) -> None:
        self._clock = clock
        self._by_user: dict[str, dict[str, Screen]] = {}
        #: The phones driving each screen, by (user, screen), then by the phone's own id. Kept
        #: beside the screens rather than on them, because a report replaces its screen whole.
        self._holds: dict[tuple[str, str], dict[str, Hold]] = {}
        #: The browser tabs holding a player or a wall that they do NOT offer, because that
        #: browser's own switch is off, by user and then tab, with when each last said so. Only
        #: that they exist is kept: a tab that is not offering says nothing about what it plays.
        self._quiet: dict[str, dict[str, float]] = {}
        self.commands = Allowance(tuning.COMMAND_BURST, tuning.COMMANDS_PER_SECOND, clock)
        self.reports = Allowance(tuning.REPORT_BURST, tuning.REPORTS_PER_SECOND, clock)

    def now(self) -> float:
        return self._clock()

    def _forget_the_silent(self) -> None:
        """Let go of every screen that has stopped reporting, for every user.

        Every user rather than the one asking, so a user who offered a screen and never came back
        leaves nothing behind: the next report from anybody clears it. The walk is over at most
        `MOST_SCREENS_PER_USER` screens for each user with one, which is small.
        """
        oldest = self._clock() - tuning.SCREEN_SECONDS
        for user_id in list(self._by_user):
            held = self._by_user[user_id]
            for screen_id in [key for key, one in held.items() if one.heard_at <= oldest]:
                del held[screen_id]
            if not held:
                del self._by_user[user_id]
        for key in list(self._holds):
            holds = self._holds[key]
            user = self._by_user.get(key[0], {})
            for controller in [name for name, one in holds.items() if one.at <= oldest]:
                del holds[controller]
            if not holds or key[1] not in user:
                del self._holds[key]
        for user_id in list(self._quiet):
            tabs = self._quiet[user_id]
            for tab in [key for key, at in tabs.items() if at <= oldest]:
                del tabs[tab]
            if not tabs:
                del self._quiet[user_id]

    def report(self, user_id: str, screen_id: str, report: Report) -> bool:
        """Record what a screen says. True when the phone should be told something moved.

        A report that only carries the position on by the time since the last one is a heartbeat
        and tells nobody anything; one that changes anything else, or moves the position further
        than playing would have (somebody sought), is news.
        """
        self._forget_the_silent()
        now = self._clock()
        # A tab that offers is no longer one that holds something back.
        self.speak_up(user_id, screen_id)
        held = self._by_user.setdefault(user_id, {})
        before = held.get(screen_id)
        held[screen_id] = Screen(user_id, screen_id, report, now)
        if before is None and len(held) > tuning.MOST_SCREENS_PER_USER:
            quietest = min(held.values(), key=lambda one: one.heard_at)
            del held[quietest.screen_id]
        if before is None:
            return True
        expected = before.position_at(now)
        moved = abs(report.position - expected) > tuning.DRIFT_SECONDS
        same = _without_position(before.report) == _without_position(report)
        return moved or not same

    def hold(self, user_id: str, screen_id: str, controller: str, label: str) -> bool | None:
        """A phone says it is driving one of its user's screens. None when there is no such screen.

        True when the screen should be told something moved: a phone it had not heard from, or one
        that now calls itself something else. A renewal is a heartbeat and tells nobody anything.
        A phone that stops renewing is let go of after `SCREEN_SECONDS`, like a quiet screen.
        """
        if self.find(user_id, screen_id) is None:
            return None
        holds = self._holds.setdefault((user_id, screen_id), {})
        before = holds.get(controller)
        if before is None and len(holds) >= tuning.MOST_SCREENS_PER_USER:
            quietest = min(holds, key=lambda name: holds[name].at)
            del holds[quietest]
        holds[controller] = Hold(label, self._clock())
        return before is None or before.label != label

    def let_go(self, user_id: str, screen_id: str, controller: str) -> bool:
        """A phone stops driving a screen. True when it had been."""
        holds = self._holds.get((user_id, screen_id))
        if holds is None or holds.pop(controller, None) is None:
            return False
        if not holds:
            del self._holds[(user_id, screen_id)]
        return True

    def controlled_by(self, user_id: str, screen_id: str) -> list[str]:
        """What the phones driving a screen are called, the most recently heard first."""
        self._forget_the_silent()
        holds = self._holds.get((user_id, screen_id), {})
        newest = sorted(holds.items(), key=lambda item: (-item[1].at, item[0]))
        return list(dict.fromkeys(one.label for _, one in newest))

    def withdraw(self, user_id: str, screen_id: str) -> bool:
        """A screen that is no longer offered. True when there was one to take away."""
        self._holds.pop((user_id, screen_id), None)
        held = self._by_user.get(user_id)
        if held is None or held.pop(screen_id, None) is None:
            return False
        if not held:
            del self._by_user[user_id]
        return True

    def keep_quiet(self, user_id: str, tab: str) -> bool:
        """A browser tab holds a player or a wall and does not offer it. True when that is news.

        Renewed like a report, and let go of after `SCREEN_SECONDS` of silence, so a tab closed
        without a word stops counting within half a minute. A renewal tells nobody anything.
        """
        self._forget_the_silent()
        tabs = self._quiet.setdefault(user_id, {})
        news = tab not in tabs
        if news and len(tabs) >= tuning.MOST_SCREENS_PER_USER:
            del tabs[min(tabs, key=lambda key: tabs[key])]
        tabs[tab] = self._clock()
        return news

    def speak_up(self, user_id: str, tab: str) -> bool:
        """A tab stops holding back: it closed its player, or it offers now. True when it had been."""
        tabs = self._quiet.get(user_id)
        if tabs is None or tabs.pop(tab, None) is None:
            return False
        if not tabs:
            del self._quiet[user_id]
        return True

    def not_offering(self, user_id: str) -> int:
        """How many of this user's browser tabs hold a player or a wall they do not offer."""
        self._forget_the_silent()
        return len(self._quiet.get(user_id, {}))

    def of(self, user_id: str) -> list[Screen]:
        """This user's screens that are still speaking, the most recently heard first."""
        self._forget_the_silent()
        held = self._by_user.get(user_id, {})
        return sorted(held.values(), key=lambda one: (-one.heard_at, one.screen_id))

    def find(self, user_id: str, screen_id: str) -> Screen | None:
        """One of this user's screens, or None: never theirs, gone silent, or never offered."""
        self._forget_the_silent()
        return self._by_user.get(user_id, {}).get(screen_id)


def _without_position(report: Report) -> Report:
    """A report with its position taken out, for asking whether anything else changed."""
    return replace(report, position=0.0)


#: The screens this process holds. Published at start-up beside playback, which is what it controls.
SCREENS: Part[Screens] = Part("remote_screens")
