# SPDX-License-Identifier: AGPL-3.0-or-later
"""Telling open connections that what they draw has changed, almost never what changed.

The client re-reads through the ordinary routes, so a message never answers who may see what."""

from __future__ import annotations

import asyncio
import secrets
import time
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum

from sift.kernel.audience import EVERY_ADMIN, Audience
from sift.kernel.db import Connection, Database, after_commit
from sift.kernel.log import get_logger
from sift.kernel.wire import Wire

log = get_logger(__name__)


LIVE_INTERVAL_SECONDS = 1.0
"""How often an open connection is looked at: what has gathered is sent, and the user behind it
is re-read.

The two halves are the same number on purpose and neither is free to move alone. Under a second a
change stops reading as live; over it, the second half is what suffers: this is also how long a
socket belonging to somebody who has just been demoted, disabled or signed out may outlive that,
because it is the interval at which the answer is asked for again.

It is not the case that an idle connection costs nothing. It costs the re-check: two small reads a
second, and one small write a minute to record that the session was seen. That is what a per-user
limit on open connections exists to bound. What it is NOT is the page read per screen per second
that polling costs, which is the whole reason this exists."""


MAX_OPINIONS_PER_BEAT = 100
"""How many of a user's own opinions may travel in one message.

There is no limit on how fast they can be written (setting a rating on a selection is one request
per file, so a large selection produces hundreds in a second) and a message is not the place to
find that out. Past this the message says only that there were more, and the screens drawing those
files re-read instead: a page is one read whatever it holds, so beyond a certain number re-reading
is both cheaper and more correct than carrying the rows.

A hundred is comfortably more than a person produces by hand and comfortably less than a page."""


MAX_COMMANDS_PER_BEAT = 20
"""How many remote commands one connection holds before the oldest are let go.

A command waits here for at most `COMMAND_SECONDS` and the route that accepts them is rate
limited, so this is a floor under a fault rather than a number a person reaches: a thumb on a
phone produces a few a second. Past it the OLDEST go, because the newest is what the person is
pressing now and a remote that dropped the latest press would act on a stale one instead."""


COMMAND_SECONDS = 4.0
"""How long a remote command waits for the screen it names, in seconds of this process's clock.

A pause is only worth delivering while the person who pressed it is still looking at the result.
A desktop that has gone to sleep, or a tab frozen in the background, wakes minutes later, and a
command still waiting then would pause a film somebody has since started again by hand. Four
seconds covers a slow network and a beat; past it the phone's own view (the screen stopped
answering) is the truthful one.

Read from the monotonic clock rather than the wall clock, because a wall clock can step backwards
whenever the machine corrects it, and an expiry read off it would either never come or come
early."""


class About(StrEnum):
    """What a message is about: one connection carries every kind, told apart by this."""

    #: What this user may see has changed; every screen holding scoped things re-reads.
    LIBRARY = "library"

    #: Files or their pictures; apart from `LIBRARY` so an import re-reads only file surfaces.
    ARRIVALS = "arrivals"

    #: Which files share a song: only a file page's Same music strip re-reads, never a wall.
    SAME_MUSIC = "same_music"

    JOBS = "jobs"

    DOWNLOADS = "downloads"

    SETTINGS = "settings"

    #: One of this user's own lists changed elsewhere.
    MINE = "mine"

    #: This user's opinions of files, carried with the message so a star costs no page read.
    OPINIONS = "opinions"

    #: One of this user's screens offered to their phone changed.
    SCREENS = "screens"

    #: A command from this user's phone, carried with the message. See `RemoteCommand`.
    REMOTE = "remote"


class AssetOpinion(Wire):
    """What one user thinks of one file: the same shape the rating and heart routes reply with."""

    asset_id: str
    favorite: bool
    rating: int | None
    views: int
    #: Required, so a generated client never treats it as optional.
    pinned: bool
    #: Required for the same reason, and carried even by writes that cannot change it.
    o_count: int


class RemoteAction(StrEnum):
    """What a phone may ask a screen to do, spelled as the desktop's shortcut table names it.

    A toggle carries the state wanted in `value`, never "flip it", as the desk may act first."""

    PLAY_PAUSE = "player.playPause"
    #: By `value` seconds.
    BACK = "player.back"
    FORWARD = "player.forward"
    #: To `value` seconds from the start: the scrubber.
    SEEK_TO = "player.seekTo"
    PREVIOUS = "player.previous"
    NEXT = "player.next"
    #: A whole percent in `value`.
    VOLUME_TO = "player.volumeTo"
    MUTE = "player.mute"
    FILL = "player.fill"
    FAVORITE = "player.favorite"
    COUNT = "player.count"
    THEATER_PAUSE_ALL = "theater.pauseAll"
    THEATER_MUTE_ALL = "theater.muteAll"
    #: Counted from 0 in `value`.
    THEATER_CELL = "theater.cell"
    THEATER_PREVIOUS = "theater.previous"
    THEATER_NEXT = "theater.next"
    #: `value` 0 stops at the end, 1 plays through, 2 repeats this.
    REPEAT = "player.repeat"
    SHUFFLE = "player.shuffle"
    #: The A-B loop's next mark: its start, then its end, then cleared.
    LOOP = "player.loop"
    SAVE_LOOP = "player.saveLoop"
    #: The last `value` seconds kept as a new file.
    CLIP = "player.clip"
    RANDOM = "player.random"
    #: By its place in the list the screen reported.
    QUALITY = "player.quality"
    CORNER = "player.corner"
    THEATER_PAUSE = "theater.pause"
    THEATER_MUTE = "theater.mute"
    THEATER_BACK = "theater.back"
    THEATER_FORWARD = "theater.forward"
    THEATER_SEEK_TO = "theater.seekTo"
    THEATER_VOLUME_TO = "theater.volumeTo"
    THEATER_REPEAT = "theater.repeat"
    THEATER_SHUFFLE = "theater.shuffle"
    THEATER_LOOP = "theater.loop"
    THEATER_SAVE_LOOP = "theater.saveLoop"
    THEATER_RANDOM = "theater.random"
    THEATER_QUALITY = "theater.quality"
    THEATER_SOLO = "theater.solo"
    #: In `value` seconds; 0 for no limit.
    THEATER_TIMER = "theater.timer"
    THEATER_EVERY_CELL = "theater.everyCell"
    #: By its place in the list the wall reported.
    THEATER_LAYOUT = "theater.layout"
    THEATER_PRESET = "theater.preset"
    THEATER_CORNER = "theater.corner"


class RemoteCommand(Wire):
    """One command from a phone; safe to carry, as none of it is a permission."""

    #: Reported back by the screen, so the phone can tell done from never heard.
    id: str
    screen: str
    action: RemoteAction
    value: float | None


@dataclass(frozen=True, slots=True)
class Pending:
    """Everything one connection has to be told on this beat, once its own user is known."""

    #: Stable order, so two connections told the same thing get the same message.
    about: tuple[About, ...]
    opinions: tuple[AssetOpinion, ...]
    more_opinions: bool
    commands: tuple[RemoteCommand, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.about)


NOTHING_PENDING = Pending(about=(), opinions=(), more_opinions=False)


class Subscription:
    """One open connection: two browsers of one user may differ in what they may see."""

    __slots__ = ("_commands", "_more_opinions", "_opinions", "_waiting", "user_id", "wake")

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        #: Subject -> admins only; a user named outright is owed it whatever its role.
        self._waiting: dict[About, bool] = {}
        self._opinions: list[AssetOpinion] = []
        self._more_opinions = False
        self._commands: list[tuple[float, RemoteCommand]] = []
        #: Set for what must not wait out the beat; the loop waits on it and its timer.
        self.wake = asyncio.Event()

    def note(self, about: About, *, admins_only: bool) -> None:
        """Something this connection is drawing has changed. Sent on its next beat."""
        if admins_only and about in self._waiting:
            return
        self._waiting[about] = admins_only

    def note_opinion(self, opinion: AssetOpinion) -> None:
        """One of this user's own opinions; past the cap only the fact of more is kept."""
        if len(self._opinions) >= MAX_OPINIONS_PER_BEAT:
            self._more_opinions = True
            return
        self._opinions.append(opinion)

    def note_command(self, command: RemoteCommand, *, expires_at: float) -> None:
        """A command for one of this user's screens, held here so a later tab never gets it."""
        self._commands.append((expires_at, command))
        del self._commands[:-MAX_COMMANDS_PER_BEAT]
        self._waiting[About.REMOTE] = False
        self.wake.set()

    def take(self, *, as_admin: bool, now: float | None = None) -> Pending:
        """Take what has gathered, applying the caller's freshest reading of the role here."""
        self.wake.clear()
        if not self._waiting and not self._opinions and not self._more_opinions:
            return NOTHING_PENDING
        moment = time.monotonic() if now is None else now
        # Expired commands are dropped at sending, the one reading of too late that is fresh.
        commands = tuple(command for expires, command in self._commands if expires > moment)
        self._commands.clear()
        if not commands:
            self._waiting.pop(About.REMOTE, None)
        about = tuple(
            sorted(
                subject
                for subject, admins_only in self._waiting.items()
                if as_admin or not admins_only
            )
        )
        opinions = tuple(self._opinions)
        more = self._more_opinions
        self._waiting.clear()
        self._opinions.clear()
        self._more_opinions = False
        if not about:
            # Nothing this connection may hear; its opinions had no subject to travel under.
            return NOTHING_PENDING
        return Pending(about=about, opinions=opinions, more_opinions=more, commands=commands)


class ChangeBus:
    """Who is connected and what each waits to be told; it resolves no permissions."""

    def __init__(self) -> None:
        self._by_user: dict[str, list[Subscription]] = {}
        self._published = 0
        self._by_subject: Counter[About] = Counter()
        #: How many of the `ARRIVALS` were a file gaining or losing a picture and nothing else.
        self._pictures = 0
        self._boot = secrets.token_hex(4)

    @property
    def mark(self) -> str:
        """Boot token and global announcement count; two marks are only compared for equality."""
        return f"{self._boot}:{self._published}"

    def mark_of(self, subjects: Iterable[About], *, pictures: bool = True) -> str:
        """Like `mark`, for `subjects` alone; without `pictures`, a picture does not count."""
        counts = ".".join(
            str(
                self._by_subject[one]
                - (self._pictures if one is About.ARRIVALS and not pictures else 0)
            )
            for one in sorted(set(subjects))
        )
        return f"{self._boot}:{counts}"

    def subscribe(self, user_id: str) -> Subscription:
        """Register a newly opened connection. The caller must release it when it closes."""
        subscription = Subscription(user_id)
        self._by_user.setdefault(user_id, []).append(subscription)
        return subscription

    def release(self, subscription: Subscription) -> None:
        """Forget a closed connection, and its user's entry with its last one."""
        open_now = self._by_user.get(subscription.user_id)
        if open_now is None:
            return
        if subscription in open_now:
            open_now.remove(subscription)
        if not open_now:
            del self._by_user[subscription.user_id]

    def open_for(self, user_id: str) -> int:
        """How many connections this user has open, for the per-user security limit."""
        return len(self._by_user.get(user_id, ()))

    def open_connections(self) -> int:
        """How many connections are open in total. For the health screen and for tests."""
        return sum(len(open_now) for open_now in self._by_user.values())

    def _everyone(self) -> list[Subscription]:
        return [one for open_now in self._by_user.values() for one in open_now]

    def publish(self, audience: Audience, about: About, *, picture: bool = False) -> None:
        """Mark this change waiting for everyone it moved; each connection sends on its beat."""
        self._published += 1
        self._by_subject[about] += 1
        self._pictures += picture
        if audience.every_admin:
            for subscription in self._everyone():
                subscription.note(about, admins_only=True)
        for user_id in audience.users:
            for subscription in self._by_user.get(user_id, ()):
                subscription.note(about, admins_only=False)

    def publish_opinion(self, user_id: str, opinion: AssetOpinion) -> None:
        """Carry one of a user's own opinions to their other connections, and only theirs."""
        self._published += 1
        self._by_subject[About.OPINIONS] += 1
        for subscription in self._by_user.get(user_id, ()):
            subscription.note(About.OPINIONS, admins_only=False)
            subscription.note_opinion(opinion)

    def publish_command(self, user_id: str, command: RemoteCommand) -> int:
        """Wake the user's connections with a command; the mark stays, so it never replays."""
        expires_at = time.monotonic() + COMMAND_SECONDS
        told = self._by_user.get(user_id, ())
        for subscription in told:
            subscription.note_command(command, expires_at=expires_at)
        return len(told)


#: A module handle, since writes everywhere would otherwise carry a bus; None is ordinary.
_LISTENER: ChangeBus | None = None


def listens(bus: ChangeBus | None) -> None:
    """Set the bus this process announces to, or clear it at shutdown."""
    global _LISTENER
    _LISTENER = bus


def current_mark() -> str | None:
    """Where the announcements stand now, or None so a reader with no bus keeps nothing."""
    return None if _LISTENER is None else _LISTENER.mark


def mark_of(subjects: Iterable[About], *, pictures: bool = True) -> str | None:
    """Like `ChangeBus.mark_of`, or None when nothing is listening."""
    return None if _LISTENER is None else _LISTENER.mark_of(subjects, pictures=pictures)


def announce(audience: Audience, about: About) -> None:
    """Tell everyone whose view this change moved, once the write it is called in commits."""
    after_commit(lambda: _deliver(audience, about))


#: Set at start-up, as the permission layer sits above this; None reaches admins alone.
_ARRIVALS: Callable[[Connection], Awaitable[Audience]] | None = None


def resolves_arrivals(resolve: Callable[[Connection], Awaitable[Audience]] | None) -> None:
    """Set how the audience for an arriving file is worked out, or clear it."""
    global _ARRIVALS
    _ARRIVALS = resolve


async def announce_arrival(connection: Connection) -> None:
    """Tell every admin and every user given anything that files arrived, left or moved."""
    announce(await who_may_see_a_file(connection), About.ARRIVALS)


async def announce_picture(connection: Connection) -> None:
    """A file gained or lost a picture: an arrival to every screen, told apart in `mark_of`."""
    audience = await who_may_see_a_file(connection)
    after_commit(lambda: _deliver(audience, About.ARRIVALS, picture=True))


async def who_may_see_a_file(connection: Connection) -> Audience:
    """Every admin and every user given anything: the cheap wide question, not the resolver's."""
    resolve = _ARRIVALS
    return EVERY_ADMIN if resolve is None else (await resolve(connection)).widened_to_admins()


Told = Audience | Callable[[Connection], Awaitable[Audience]]


@asynccontextmanager
async def telling(database: Database, audience: Told, about: About) -> AsyncIterator[Connection]:
    """A write that tells whoever draws what it changed, on commit and only if a row moved."""
    async with database.write() as connection:
        before = connection.total_changes
        yield connection
        if connection.total_changes != before:
            told = audience if isinstance(audience, Audience) else await audience(connection)
            announce(told, about)


def announce_now(audience: Audience, about: About) -> None:
    """Tell everyone about a change that was never written, such as download progress."""
    _deliver(audience, about)


def announce_opinion(user_id: str, opinion: AssetOpinion) -> None:
    """Carry a user's own opinion of a file to their other screens, once the write has landed."""
    after_commit(lambda: _deliver_opinion(user_id, opinion))


def _deliver(audience: Audience, about: About, *, picture: bool = False) -> None:
    """Hand a landed change to whoever is listening. Runs after the commit, off the lock."""
    bus = _LISTENER
    if bus is None:
        return
    if picture:
        bus.publish(audience, about, picture=True)
    else:
        bus.publish(audience, about)


def _deliver_opinion(user_id: str, opinion: AssetOpinion) -> None:
    bus = _LISTENER
    if bus is None:
        return
    bus.publish_opinion(user_id, opinion)
