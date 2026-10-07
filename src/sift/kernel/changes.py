# SPDX-License-Identifier: AGPL-3.0-or-later
"""Telling the connections that are open that what they are drawing has changed.

**A message says that something changed. It almost never says what.** It names a kind of thing, and
the client asks the ordinary endpoint again, which goes through the permission layer like every
other read in the application. That is the load-bearing decision here and everything else follows
from it: a notification is not a read, so this can never become a second answer to "who may see
what", on a channel the route table cannot describe and no permission gate covers. The way an
unauthorized read gets served is exactly that two paths come to disagree.

It also costs less than it looks, for the shape of writing Sift actually does. Carrying rows would
be one message per changed row, and a scan or a sweep changes them in thousands; announcing is one
re-read of the one page a connection is drawing, however many rows moved.

**The one exception is a user's own opinion of a file**: a heart, a star, how many times it
has been opened. That is not a permission and cannot become one: it is a fact about the user
being told, written by that user a moment ago on another screen, and the screens that already
have the row apply it without asking for anything. Making a star cost a page fetch would be a
request per press of the rating control, on every screen that draws the file.

**The second exception is a command from the user's own phone** to one of their own screens:
pause, seek, the next file. It is not a read either: it names a screen the user offered and a verb
that screen said it can do, and it reaches only that user's connections. It differs from an opinion
in the two ways a command has to: it wakes the connection rather than waiting for its beat (a
pause that lands a second late is a remote that feels broken), and it expires in seconds and is
never repeated to a connection that opens later (a pause replayed to a tab reopened a minute after
is a pause nobody asked for).

**Nothing is replayed and nothing is buffered here.** A connection that has been away is not owed a
history: it is told where the stream of announcements now stands (see `ChangeBus.mark`), compares
that against where it was, and either re-reads everything or does not. A buffer would be state the
server holds on behalf of a client that may never come back, sized by a guess about how long an
absence lasts.

**What is held is small and lives only as long as the process.** One entry per open connection,
each holding a user, a handful of subject names, and at most a capped list of that user's
own opinions. Every connection wakes on its own beat to re-check its user's permission, and
sends whatever has gathered since it last looked, so a thousand changes in one second are one
message, and a second in which nothing happened is no message at all.

Access is from the event loop, which runs one thing at a time, so the plain dictionary needs no
lock: there is no await between reading it and writing it.
"""

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
    """What a message is about.

    Every socket costs a browser connection, a handshake and a permission read on every beat, so
    every kind of live update arrives on one connection and is told apart by this.

    Each one is a question a screen asks the server, never an answer. The client re-asks the
    ordinary endpoint behind whichever of these arrives.
    """

    #: What this user may see has changed: something shared, unshared, hidden, revealed, put
    #: into or taken out of a collection, renamed, or gone from the library altogether. Every
    #: screen holding a list of scoped things re-reads.
    LIBRARY = "library"

    #: Files have entered the library, left it, or moved, or one of them has gained or lost a
    #: picture (a still, a hover clip, a scrub strip). Separate from `LIBRARY` because a scan
    #: imports thousands and only the surfaces that draw files care, making every wall of
    #: people, tags and collections re-read on each beat of an import would be most of the cost of
    #: polling, for nothing. A picture belongs here for the same reason: what it changes is a tile,
    #: and a tile is what the surfaces listening for this re-read.
    ARRIVALS = "arrivals"

    #: Which files share a song: only a file page's Same music strip re-reads, never a wall.
    SAME_MUSIC = "same_music"

    #: The work queue moved. What the dashboard draws, and what tells a grid that something it is
    #: waiting for has finished.
    JOBS = "jobs"

    #: The download queue moved: something queued, finished, failed, or simply got further along.
    DOWNLOADS = "downloads"

    #: A setting the whole installation shares has changed.
    SETTINGS = "settings"

    #: A list that is this user's own has changed somewhere else: a saved search, a recent
    #: search, or what it thinks of a person, a Site, a collection or a tag. The screens holding
    #: one of those lists re-read.
    MINE = "mine"

    #: What this user thinks of particular FILES: a heart, a star, the tally of how many times
    #: one has been opened. **The only subject that carries anything with it**, and the only reason
    #: it is not `MINE` is that this one must not cost a page read: a rating control writes one of
    #: these per press, and every screen already drawing the row applies it without asking for
    #: anything. Folding the two together would put a fetch behind every star.
    OPINIONS = "opinions"

    #: One of this user's screens (an open player or a Theater wall offering itself to their
    #: phone) started, stopped, changed what it plays or went away. A question like the others:
    #: the phone re-reads its list of screens, through the route that decides what it may be told
    #: about each one.
    SCREENS = "screens"

    #: A command from this user's phone for one of their screens. **The second subject that
    #: carries something with it**: the command is the message, and there is nothing to re-read.
    #: See `RemoteCommand` for why that is safe and `COMMAND_SECONDS` for why it does not wait.
    REMOTE = "remote"

    #: A Theater wall sent from one of this user's devices to another is waiting to be taken. A
    #: question like the others: the Theater that is open on the receiving kind of device asks for
    #: it (`POST /remote/walls/take`), and the first to ask is the one that gets it.


class AssetOpinion(Wire):
    """What one user thinks of one file, as another of its own screens should draw it.

    A wire shape rather than something the feed converts into one, so there is exactly one
    description of it: the client's type is generated from the route that carries it, and a second
    internal copy of the same three fields would be a second thing to keep in step.

    The same shape the rating and heart routes reply with, and that is the point rather than a
    coincidence. A reply and a message reach the same screens by two routes (the control that
    was pressed answers this tab at once, the connection tells this user's other browsers a
    moment later) and the grid applies whichever arrives by naming the file and setting the row.
    Two shapes would mean the tab that pressed the control and the tab that did not were served by
    two different pieces of code, and the one that only runs for somebody else is the one nobody
    would notice breaking.

    So it names the file even in a reply, where the asker already knows which one it asked about,
    and it carries the tally of how many times the file has been opened even from a control that
    cannot change it. Both are the price of one description, and both are cheaper than the second
    description would be.
    """

    asset_id: str
    favorite: bool
    rating: int | None
    views: int
    #: Kept at the top of its wall by this user. Required rather than defaulted, for the reason
    #: the whole shape is one shape: a field with a default reads to a generated client as one the
    #: server might not send, and every screen that draws an opinion has to draw this one.
    pinned: bool
    #: How many times this user has pressed the O mark on this file. Required, for the reason
    #: the pin above it is: a screen drawing one of these draws all of it, and a field a generated
    #: client thinks might be missing is a number a tile has to guess at.
    #:
    #: Carried by every write here, including the ones that cannot change it (the heart, the
    #: stars, the pin), which is the price of there being ONE description of what this user
    #: thinks of a file rather than one per control.
    o_count: int


class RemoteAction(StrEnum):
    """What a phone may ask one of its screens to do.

    **Spelled exactly as the desktop names the same verb** (`player.*` and `theater.*` in the
    client's table of shortcuts), because on the desktop one table of actions answers both the
    keyboard and the phone: a key and a command reach the same line of code by the same name, so
    a verb cannot work from one and not the other.

    Where the key is a toggle, the command carries the state wanted (`value` 1 or 0) rather than
    "flip it". Both sides act on the same player: somebody at the desk pausing a moment before
    the phone presses pause would otherwise have the phone's press start it again.
    """

    #: Playing (value 1) or paused (value 0).
    PLAY_PAUSE = "player.playPause"
    #: A step back or on, by `value` seconds.
    BACK = "player.back"
    FORWARD = "player.forward"
    #: To `value` seconds from the start: the scrubber.
    SEEK_TO = "player.seekTo"
    #: The file before or after this one, in the list it was opened from.
    PREVIOUS = "player.previous"
    NEXT = "player.next"
    #: How loud, as a whole percent in `value`.
    VOLUME_TO = "player.volumeTo"
    #: Muted (value 1) or not (value 0).
    MUTE = "player.mute"
    #: Filling the screen (value 1) or not (value 0), where the screen said it can.
    FILL = "player.fill"
    #: A favourite (value 1) or not (value 0): the viewer's own, whatever it is showing.
    FAVORITE = "player.favorite"
    #: One more on the O counter, for the file being shown.
    COUNT = "player.count"
    #: The whole wall stopped (value 1) or playing (value 0).
    THEATER_PAUSE_ALL = "theater.pauseAll"
    #: The whole wall silenced (value 1) or not (value 0).
    THEATER_MUTE_ALL = "theater.muteAll"
    #: The cell to talk to, counted from 0 in `value`.
    THEATER_CELL = "theater.cell"
    #: The file before or after, in the cell being talked to.
    THEATER_PREVIOUS = "theater.previous"
    THEATER_NEXT = "theater.next"
    #: The rest of what the player's bar and drawer offer, so the phone reaches every one of them.
    #: What happens at the end: `value` is the place in the one order the repeat press cycles
    #: through (0 stop at the end, 1 play through, 2 repeat this).
    REPEAT = "player.repeat"
    #: Shuffled (value 1) or in order (value 0).
    SHUFFLE = "player.shuffle"
    #: The A-B loop's next mark: its start, then its end, then cleared.
    LOOP = "player.loop"
    #: The marked stretch kept as a Loop.
    SAVE_LOOP = "player.saveLoop"
    #: The last `value` seconds kept as a new file.
    CLIP = "player.clip"
    #: Something else, out of the whole library.
    RANDOM = "player.random"
    #: A size of the file, by its place in the list the screen reported.
    QUALITY = "player.quality"
    #: Into the corner player, or back to full size.
    CORNER = "player.corner"
    #: The rest of what a wall offers: the cell being talked to (or every cell, where the wall is
    #: addressing all of them), its sound, its place in its file, and its drawer.
    #: The cell held (value 1) or playing (value 0).
    THEATER_PAUSE = "theater.pause"
    #: The cell muted (value 1) or not (value 0).
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
    #: Hear only the cell being talked to.
    THEATER_SOLO = "theater.solo"
    #: How long the cell holds a file before it moves on, in `value` seconds; 0 for no limit.
    THEATER_TIMER = "theater.timer"
    #: Talk to every cell at once.
    THEATER_EVERY_CELL = "theater.everyCell"
    #: A layout or a saved preset, by its place in the list the wall reported.
    THEATER_LAYOUT = "theater.layout"
    THEATER_PRESET = "theater.preset"
    THEATER_CORNER = "theater.corner"


class RemoteCommand(Wire):
    """One command from a phone, as the screen it names receives it.

    Safe to carry for the reason an opinion is: none of it is a permission. The screen named is
    one this user offered, the verb is one it said it can do, and it travels only to this user's
    own connections. Every connection of the user receives it and only the tab that offered the
    screen acts, which costs one small message per tab and needs no second address on the socket.
    """

    #: Minted by the server when the command was accepted. The screen reports the last one it acted
    #: on, which is how a phone tells "done" from "the screen never heard".
    id: str
    screen: str
    action: RemoteAction
    value: float | None


@dataclass(frozen=True, slots=True)
class Pending:
    """Everything one connection has to be told on this beat, once its own user is known."""

    #: The subjects, in a stable order so that two connections told the same thing are told it the
    #: same way, which is the difference between a test that can assert on a message and one that
    #: can only count.
    about: tuple[About, ...]
    #: This user's own opinions, at most `MAX_OPINIONS_PER_BEAT` of them.
    opinions: tuple[AssetOpinion, ...]
    #: Whether more were written than could be carried. The screens drawing files re-read when so.
    more_opinions: bool
    #: Commands from this user's phone that have not yet expired, oldest first.
    commands: tuple[RemoteCommand, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.about)


#: What a connection with nothing waiting takes. Named so an idle beat allocates nothing.
NOTHING_PENDING = Pending(about=(), opinions=(), more_opinions=False)


class Subscription:
    """One open connection's place in the bus.

    Keyed by user for matching and never by session, because an audience is a set of users,
    but there is one of these per CONNECTION, not per user, because two browsers signed in as
    one user can legitimately differ in what they may see: opening Hidden is something one
    browser did.
    """

    __slots__ = ("_commands", "_more_opinions", "_opinions", "_waiting", "user_id", "wake")

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        #: Subject -> whether it may only be sent to an admin. A subject reaching one connection
        #: both ways in the same beat keeps the unconditional reading: a user who was named
        #: outright is owed the message whatever its role turns out to be.
        self._waiting: dict[About, bool] = {}
        self._opinions: list[AssetOpinion] = []
        self._more_opinions = False
        #: Commands waiting for this connection, each with the moment it stops being worth sending.
        self._commands: list[tuple[float, RemoteCommand]] = []
        #: Set when something arrives that must not wait out the beat. The connection's loop waits
        #: on this as well as on its timer, which is the whole of what an early beat is.
        self.wake = asyncio.Event()

    def note(self, about: About, *, admins_only: bool) -> None:
        """Something this connection is drawing has changed. Sent on its next beat."""
        if admins_only and about in self._waiting:
            return
        self._waiting[about] = admins_only

    def note_opinion(self, opinion: AssetOpinion) -> None:
        """One of this user's own opinions, to apply without asking for anything.

        Past the cap the opinion itself is dropped and the fact that there were more is kept, which
        is what turns a bulk rating into one re-read rather than a message the size of a page.
        """
        if len(self._opinions) >= MAX_OPINIONS_PER_BEAT:
            self._more_opinions = True
            return
        self._opinions.append(opinion)

    def note_command(self, command: RemoteCommand, *, expires_at: float) -> None:
        """A command for one of this user's screens, sent at once rather than on the next beat.

        Held on THIS connection and nowhere else, which is what keeps it from being repeated to a
        tab that connects later: a new connection starts with nothing waiting. Past the cap the
        oldest goes, for the reason `MAX_COMMANDS_PER_BEAT` gives.
        """
        self._commands.append((expires_at, command))
        del self._commands[:-MAX_COMMANDS_PER_BEAT]
        self._waiting[About.REMOTE] = False
        self.wake.set()

    def take(self, *, as_admin: bool, now: float | None = None) -> Pending:
        """What has gathered since the last look, for a user who is what they say, and clear it.

        **The role is applied HERE and nowhere else, and it is the caller's freshest reading.** A
        subject that only an admin may hear was marked on every connection when it was published,
        because the bus holds no roles: asking who was an admin at publishing time would be a second
        copy of that answer, believed for as long as the message waited. Asking at the moment of
        sending costs nothing (the connection re-reads its own user on every beat regardless)
        and cannot be stale.
        """
        self.wake.clear()
        if not self._waiting and not self._opinions and not self._more_opinions:
            return NOTHING_PENDING
        moment = time.monotonic() if now is None else now
        # A command that waited past its time is dropped here, at the moment of sending, which is
        # the one reading of "too late" that cannot be stale. See `COMMAND_SECONDS`.
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
            # Nothing this connection may hear. Its opinions go with it: `OPINIONS` is the subject
            # they travel under, and it is not admin-only, so an empty subject list here means there
            # was nothing to carry them.
            return NOTHING_PENDING
        return Pending(about=about, opinions=opinions, more_opinions=more, commands=commands)


class ChangeBus:
    """Who is connected, and what each of them is waiting to be told.

    One per running application. It holds no database, opens nothing and answers no question about
    permissions: an audience arrives already resolved, by the write that resolved it, or naming a
    reach whose one open question is settled at the moment of sending.

    **It also counts what it has published**, which is what a browser that was away compares itself
    against. See `mark`.
    """

    def __init__(self) -> None:
        self._by_user: dict[str, list[Subscription]] = {}
        #: How many announcements this bus has handed out. Only ever compared with itself.
        self._published = 0
        #: The same count kept per subject. See `mark_of`.
        self._by_subject: Counter[About] = Counter()
        #: What tells one run of the application from the next. See `mark`.
        self._boot = secrets.token_hex(4)

    @property
    def mark(self) -> str:
        """Where this application's stream of announcements stands, as something to compare.

        **What a reconnecting browser is answered with, and the whole of how it learns it missed
        something.** It says one thing (has anything been announced since you were last spoken
        to) and the only honest thing to do with two of them is ask whether they are the same.

        THE COUNT IS GLOBAL RATHER THAN PER-USER, and that is forced rather than chosen. Half
        the announcements in Sift go to `EVERY_ADMIN`, which is a reach and not a list: this bus
        holds no database and may not ask who the admins are, so a user who is not connected
        cannot have a counter of its own raised on its behalf. A global count is the one that can
        never say "nothing moved" when something did. It costs a browser one extra re-read on a
        reconnect that followed somebody else's change, which is exactly what it would do if there
        were no mark at all.

        THE BOOT TOKEN IS NOT DECORATION. The count lives as long as the process, so without it a
        restarted application would start again at zero and a browser holding a low count could be
        told, truthfully and uselessly, that nothing had moved. A publish that a stopping process
        never delivered is the case this exists for: the change is stored, the message is lost, and
        the next handshake is the only chance anybody has to notice.

        Nothing is derived from it and nothing may be: it is not a length, not a time and not an
        order. Two of these can only be equal or not.
        """
        return f"{self._boot}:{self._published}"

    def mark_of(self, subjects: Iterable[About]) -> str:
        """Where the announcements about `subjects` alone stand, compared like `mark`.

        For an answer only some subjects can change. Kept under `mark` it is thrown away by work
        that could not have moved it: the job queue is announced several times a second while
        anything runs.
        """
        counts = ".".join(str(self._by_subject[one]) for one in sorted(set(subjects)))
        return f"{self._boot}:{counts}"

    def subscribe(self, user_id: str) -> Subscription:
        """Register a newly opened connection. The caller must release it when it closes."""
        subscription = Subscription(user_id)
        self._by_user.setdefault(user_id, []).append(subscription)
        return subscription

    def release(self, subscription: Subscription) -> None:
        """Forget a connection that has closed.

        The user's own entry goes too once its last connection has gone, so an install that has
        had a thousand guests through it holds nothing for the nine hundred and ninety nine who
        left.
        """
        open_now = self._by_user.get(subscription.user_id)
        if open_now is None:
            return
        if subscription in open_now:
            open_now.remove(subscription)
        if not open_now:
            del self._by_user[subscription.user_id]

    def open_for(self, user_id: str) -> int:
        """How many connections this user already has open.

        What the per-user limit is checked against. The limit is a security control rather than
        a tuning knob: every connection costs a permission read every second, and opening them
        needs no credential beyond the one a user was already given.
        """
        return len(self._by_user.get(user_id, ()))

    def open_connections(self) -> int:
        """How many connections are open in total. For the health screen and for tests."""
        return sum(len(open_now) for open_now in self._by_user.values())

    def _everyone(self) -> list[Subscription]:
        return [one for open_now in self._by_user.values() for one in open_now]

    def publish(self, audience: Audience, about: About) -> None:
        """Mark this change waiting for everyone whose view it moved.

        Nothing is sent from here. Each connection sends on its own beat, which is what turns a
        sweep's thousands of changes into one message and what keeps a slow reader from setting
        everybody else's pace.
        """
        self._published += 1
        self._by_subject[about] += 1
        if audience.every_admin:
            for subscription in self._everyone():
                subscription.note(about, admins_only=True)
        for user_id in audience.users:
            for subscription in self._by_user.get(user_id, ()):
                subscription.note(about, admins_only=False)

    def publish_opinion(self, user_id: str, opinion: AssetOpinion) -> None:
        """Carry one of a user's own opinions to their other connections.

        Only ever to the user it belongs to, which is why this takes an id rather than an
        audience: there is no version of this that reaches anybody else, and a signature that could
        express one would be a signature somebody could get wrong.
        """
        self._published += 1
        self._by_subject[About.OPINIONS] += 1
        for subscription in self._by_user.get(user_id, ()):
            subscription.note(About.OPINIONS, admins_only=False)
            subscription.note_opinion(opinion)

    def publish_command(self, user_id: str, command: RemoteCommand) -> int:
        """Carry a command from a user's phone to that user's connections, now.

        Only ever to the user it belongs to, for the reason `publish_opinion` takes an id. Every one
        of the user's connections is woken, and the one whose tab offered the screen acts on it.

        **The mark is NOT moved**, unlike every other publish here. The mark is what a reconnecting
        browser compares to decide whether to re-read its screens, and a command is not something
        a screen could have missed and should repair: repairing it would be replaying it, which is
        the one thing a command must never do. Returns how many connections were told. That is not
        whether the screen heard it (the phone's own connection is one of them), which only the
        screen can say, by reporting the command's id back.
        """
        expires_at = time.monotonic() + COMMAND_SECONDS
        told = self._by_user.get(user_id, ())
        for subscription in told:
            subscription.note_command(command, expires_at=expires_at)
        return len(told)


#: The bus this process is announcing to, or None where nothing is listening.
#:
#: A module-level handle rather than something passed down, and the reason is where the
#: announcements come from: writes spread across the permission layer, the content layer and most
#: of the features, many of them plain functions that are handed a connection and nothing else.
#: Threading a bus through all of them would put a live-update parameter on the signature of every
#: function in Sift that changes what somebody may see.
#:
#: None is an ordinary state and not a failure. A console tool, a migration and most of the test
#: suite run with nobody listening, and a change announced then is simply a change nobody was
#: connected for.
_LISTENER: ChangeBus | None = None


def listens(bus: ChangeBus | None) -> None:
    """Set the bus this process announces to, or clear it.

    Called by the composition root at start-up and again at shutdown. Clearing it matters: a bus
    left behind by an application that has stopped would go on collecting announcements for
    connections that closed with it.
    """
    global _LISTENER
    _LISTENER = bus


def current_mark() -> str | None:
    """Where the announcements stand right now, or None when nothing is listening.

    For a reader that wants to keep an answer until something changes: the mark moves on every
    announcement, so an answer kept under it is exactly as fresh as the last announcement. None
    rather than a fixed word when there is no bus, so a reader with nothing to compare against
    keeps nothing rather than keeping it for ever.
    """
    return None if _LISTENER is None else _LISTENER.mark


def mark_of(subjects: Iterable[About]) -> str | None:
    """Where the announcements about `subjects` stand right now, or None when nothing is
    listening. See `ChangeBus.mark_of`, and `current_mark` for what None means."""
    return None if _LISTENER is None else _LISTENER.mark_of(subjects)


def announce(audience: Audience, about: About) -> None:
    """Tell everyone whose view this change moved, once the change has actually landed.

    Must be called from inside the write that made the change, and does nothing until that write
    commits. Announcing before the commit would announce something that may still roll back;
    announcing from outside would leave a window in which the change is stored and nobody has been
    told, which on a screen looks exactly like the feature not working.

    It is a separate call from raising the user's picture counter, and that is deliberate rather
    than an oversight. The two are peers reading one answer: the counter is about copies on a
    machine Sift cannot reach, this is about a connection it is holding open, and neither may be
    reached through the other. What only the caller knows is which of the two it is: a write
    resolves the audience, and the call site names what changed.
    """
    after_commit(lambda: _deliver(audience, about))


#: How the users a newly arrived file could reach are worked out, or None where nobody has said.
#:
#: Set by the composition root at start-up, exactly as the bus above is, and for a reason of the
#: same shape. The answer lives in the permission layer, because it is read off the grant table and
#: nothing outside that layer may name it, and the permission layer is built on top of the content
#: layer that arrivals are announced from. Importing upwards to reach it would be a cycle; handing
#: it down at the one place that already holds both is not.
#:
#: None means arrivals reach admins alone, which is the honest answer for a process that was never
#: told otherwise: an admin sees the whole library, so an arrival is theirs by definition, and
#: nobody else can be worked out without asking.
_ARRIVALS: Callable[[Connection], Awaitable[Audience]] | None = None


def resolves_arrivals(resolve: Callable[[Connection], Awaitable[Audience]] | None) -> None:
    """Set how the audience for an arriving file is worked out, or clear it."""
    global _ARRIVALS
    _ARRIVALS = resolve


async def announce_arrival(connection: Connection) -> None:
    """Files have entered the library, left it, or moved. Tell whoever that could reach.

    Every admin, and every user who has been given anything at all. The second half is what
    makes a file landing in a shared folder follow the share that folder already has, rather than
    waiting for whoever is looking at it to reload the page.

    It is deliberately wider than "who can see this file". That question is the permission
    resolver, it is bound to one viewer at a time, and asking it per user per imported file
    would put it on the slowest path in the application. A second, looser copy of it would be worse
    still. So this asks the cheap question (has this user been given anything) and the
    client answers the exact one for itself by re-reading its own page through the ordinary door.

    Called from inside the write that took the file in, and does nothing until that write commits.
    """
    announce(await who_may_see_a_file(connection), About.ARRIVALS)


async def who_may_see_a_file(connection: Connection) -> Audience:
    """Everyone a change to one file could concern: every admin, and every user given anything.

    The question an arrival asks, and the same one a change to a file's own record asks (its
    title, its details, its links) because both are "who might be drawing this file". Named once
    here so both read one answer rather than one calling the other: see `announce_arrival` for why
    it is the cheap wide question and not the permission resolver's exact one, and what that costs.
    """
    resolve = _ARRIVALS
    return EVERY_ADMIN if resolve is None else (await resolve(connection)).widened_to_admins()


Told = Audience | Callable[[Connection], Awaitable[Audience]]


@asynccontextmanager
async def telling(database: Database, audience: Told, about: About) -> AsyncIterator[Connection]:
    """A write, and whoever is drawing what it changed told once it has landed.

    **The one way a write says what it did.** Almost everything on a screen in Sift is a list, and
    almost every list is read once and then kept, so a write that nobody is told about is a screen
    somewhere showing the answer from before it, until whoever is looking at it reloads the page.
    That is not a class of bug that shows up in testing: the tab that made the change is right, and
    it is every other tab that is wrong.

    So it is one wrapper rather than a line to remember at each of dozens of writes. Opening a write
    is what somebody does when they add a feature, and this is what makes that the moment they say
    who it concerns.

    **Nothing is said unless a row actually moved**, and that is measured rather than assumed. A
    great many of these writes are conditional (a cancel that races the thing finishing, a sweep
    that finds nothing stale, an update whose row is not there) and announcing on the attempt
    would have screens re-reading pages for changes that never happened. The driver counts the rows
    a connection has changed, so the difference across the block is what this transaction did, and a
    statement that matched nothing leaves it where it was.

    Announced on the commit, so a write that rolls back announces nothing.
    """
    async with database.write() as connection:
        before = connection.total_changes
        yield connection
        if connection.total_changes != before:
            told = audience if isinstance(audience, Audience) else await audience(connection)
            announce(told, about)


def announce_now(audience: Audience, about: About) -> None:
    """Tell everyone about a change that was never written down.

    Not everything a screen draws is a row. How far along a download is, is held in memory and
    changes several times a second on purpose: storing it would be writing a number nobody will
    ever read back, for a transfer that cannot survive a restart anyway. There is no commit to wait
    for, so `announce` cannot be used and would refuse: it registers work against the transaction
    in progress, and outside one there is none to register against.

    Everything else is the same, including the coalescing: several times a second in, at most once
    a second out.
    """
    _deliver(audience, about)


def announce_opinion(user_id: str, opinion: AssetOpinion) -> None:
    """Carry a user's own opinion of a file to their other screens, once the write has landed.

    The one thing that travels with a message rather than being asked for again. See this module's
    header for why that exception is narrow and why it stays narrow.
    """
    after_commit(lambda: _deliver_opinion(user_id, opinion))


def _deliver(audience: Audience, about: About) -> None:
    """Hand a landed change to whoever is listening. Runs after the commit, off the lock."""
    bus = _LISTENER
    if bus is None:
        return
    bus.publish(audience, about)


def _deliver_opinion(user_id: str, opinion: AssetOpinion) -> None:
    bus = _LISTENER
    if bus is None:
        return
    bus.publish_opinion(user_id, opinion)
