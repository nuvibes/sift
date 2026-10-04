# SPDX-License-Identifier: AGPL-3.0-or-later
"""Noticing that a file arrived, and waiting until it has finished arriving.

Drop a video into a watched folder and it should appear, without anybody pressing anything. That
is the whole of what this does, and almost all of it is the waiting.

**Why this is not a job.** A watcher runs for as long as Sift does. The queue's workers are a
small fixed number (as many as the machine can usefully run at once), and a job that never
returns keeps one of them forever. Watch four folders on a four-worker box and nothing else ever
runs again: no `probe`, no thumbnails, no scans. So the watcher lives beside the queue and puts
work into it, rather than being work in it.

**Why quiet, not events.** A file is not ready when it appears; it is ready when the program
writing it has finished. Copying a video produces a stream of events over however many seconds it
takes, and the first of them is a file of zero bytes. Hashing that gets the digest of nothing:
a permanently mis-identified asset that nothing ever revisits, because as far as Sift is concerned
it was indexed successfully.

So no single event means anything here. What means something is events *stopping*: every event
pushes a deadline out, and only when a folder has said nothing for a while is it handed to a scan.
That is the settle-wait, and it is the same mechanism as the debounce: a file still being written
is a folder that has not gone quiet. It also collapses a copy of five hundred files into one scan
instead of five hundred.

**Why the scan does the work.** The watcher decides *when*, never *what*. Once a folder is quiet it
enqueues an ordinary scan of that folder, and everything after that (the gate, the digest, the
rows, `probe`, the refusal memory) is the code that already exists and is already tested. A
watcher that indexed files itself would be a second pipeline, and the second one is always the one
that is subtly wrong.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import stat as stat_module
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path, PurePosixPath

from watchdog.events import (
    EVENT_TYPE_CLOSED,
    EVENT_TYPE_CREATED,
    EVENT_TYPE_DELETED,
    EVENT_TYPE_MODIFIED,
    EVENT_TYPE_MOVED,
    FileSystemEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver
from watchdog.observers.polling import PollingEmitter, PollingObserver

from sift.kernel.content import LibraryStore, Root, RootKind, check_rel_path
from sift.kernel.jobs import JobQueue, JobSwitchedOff
from sift.kernel.log import get_logger
from sift.kernel.paths import is_absence
from sift.kernel.wiring import Part
from sift.slices.library_roots.jobs import (
    MOST_NAMED_PATHS,
    RECONCILE,
    SCAN,
    WORTH_OPENING,
    scan_shape,
)

if sys.platform == "win32":  # pragma: no cover (one arm of it is dead on whichever platform)
    # Guarded, because the module underneath is ctypes against kernel32 and `ctypes.wintypes` does
    # not exist anywhere else. Sift ships on Windows and is meant to be readable and runnable by
    # somebody self-hosting it on a Linux box, where watchdog's own inotify observer is the right
    # answer and this file must still import.
    #
    # Excluded rather than tested: an import decided at import time cannot be taken both ways in one
    # process, so a coverage run reads whichever arm this machine did not take as a line nothing
    # reaches. What the two arms lead to IS tested (see `_observer_for`, which is asked for both).
    from sift.slices.library_roots.native_watch import SafeWindowsApiObserver

log = get_logger(__name__)

#: How long a folder has to say nothing before it is scanned. Long enough that a file being copied
#: keeps resetting it (a stalled write of a few hundred milliseconds is ordinary), and short
#: enough that dropping a file in feels immediate.
QUIET_SECONDS = 3.0

#: How often the pending folders are looked at. Not the quiet period: this is the resolution of it.
TICK_SECONDS = 0.5

#: The polling timeout used when nothing configures one: a watcher built directly in a test, or a
#: NAS root with the settings at their defaults. watchdog's own default is ten seconds, which is a
#: long time to wait for a dropped-in file to appear; five is a gentler-than-inotify middle.
DEFAULT_POLL_SECONDS = 5.0

#: How long one observer gets to let go before it is left to the process.
#:
#: Generous rather than tight: a poller mid-walk over a slow share legitimately takes seconds to
#: notice it has been asked to stop, and killing that off early is how a watch gets abandoned on a
#: machine where nothing was wrong. What this exists to bound is the case where the answer is never
#: coming at all (see `_stop_observers`).
_STOP_SECONDS = 5.0


#: The most of its time the poller may spend walking a tree, as a share of one.
#:
#: A poll interval is a promise nobody can keep on a large library over a slow filesystem, because
#: the person setting it cannot know what a pass costs: one pass over a few thousand files on a
#: network-backed mount can take **tens of seconds**, against an interval set to three, so without
#: this the poller would never stop walking, and the machine would spend every second of every day
#: re-reading a library that had not changed.
#:
#: A share is the right control because it is the only one that keeps meaning the same thing on a
#: different machine. A quarter says: noticing a new file may cost up to a quarter of the poller's
#: time, and the rest belongs to whoever is using the application. On a small library a pass is
#: milliseconds and this never binds: the configured interval is longer and simply wins, so
#: nothing changes for the ordinary case.
#:
#: Not a smaller share, because the other end of it is how long a dropped-in file takes to appear.
POLL_WALK_SHARE = 0.25

#: However expensive a pass turns out to be, wait no longer than this between them.
#:
#: The share alone has no ceiling: a slow enough tree would back off until nothing was noticed for
#: an hour, which is indistinguishable from a watcher that has stopped. Five minutes is the point
#: past which somebody would go and press Rescan.
MAX_POLL_REST_SECONDS = 300.0

#: Whether this platform's own change notifications can be used, or the tree has to be walked.
#:
#: TRUE EVERYWHERE. The fault it answers is the reason the flag exists: watchdog's native watch on
#: Windows is a synchronous `ReadDirectoryChangesW` parked in a thread of its own, and the only way
#: to end one is to close the handle it is blocked on, from a different thread, while the call is
#: still writing into a buffer it was handed. Windows says plainly that closing a handle with I/O
#: outstanding on it is undefined, and both of the things it is undefined into happen: the read
#: never returns, so stopping a watch never finishes, AND the completion lands in memory that has
#: been given back, which takes the process down with an access violation.
#:
#: Walking instead is bounded by `POLL_WALK_SHARE` (the pacing above is what makes it affordable
#: on a large library), and it is how every network share without notifications is watched. A
#: crash while somebody is using the application is not a trade against latency.
#:
#: **So `native_watch.py` does the read inside Sift.** The read is issued OVERLAPPED, so it
#: returns at once and completes into an event the emitter waits on with a timeout, which is what
#: gives the thread a moment to notice it has been asked to stop. Stopping cancels and closes
#: nothing; the handle is closed by the emitter's own thread, after its loop has ended and after the
#: cancelled read has been collected with `GetOverlappedResult(bWait)`. There is never an
#: outstanding operation at close time, so the undefined behaviour is removed rather than raced.
_NATIVE_WATCH_IS_SAFE = True

#: How long after a watch ends before the first attempt to attach it again, and the most it waits
#: between attempts after doubling. See `LibraryWatcher._reattach`.
#:
#: Not at once: the ordinary way a watch ends is a share that has just dropped, and asking a share
#: that is not there a question every second is a stream of timeouts over a network that is already
#: in trouble. Doubling from five seconds reaches the ceiling in six attempts, about five minutes:
#: the same ceiling the poller's rest has, and for its reason: past it, a share that came back looks
#: like a watcher that has stopped, and somebody goes and presses Rescan.
REATTACH_FIRST_SECONDS = 5.0
REATTACH_MOST_SECONDS = MAX_POLL_REST_SECONDS

#: How long to wait before asking a second time whether a file has stopped growing.
#:
#: Deliberately much shorter than the settle-wait. See `_sweep_pending` for why they are two
#: different questions: by the time this is used, the folder has ALREADY been quiet for a full
#: settle-wait, and all that is left is to confirm the bytes have stopped as well.
_SETTLE_RECHECK_SECONDS = 0.5

#: How long a native watch waits before looking up to see whether it has been asked to stop.
#:
#: Nothing is polled at this rate and it has no effect on how quickly a file is noticed: a change
#: signals the wait immediately, and a stop cancels the read, which signals it too. All it bounds is
#: how long a thread with nothing to do sleeps between two checks it will almost always pass.
_STOP_CHECK_SECONDS = 1.0

#: Whether the native watch here is Sift's own emitter or watchdog's. See `_observer_for`.
_WINDOWS = sys.platform == "win32"


class _PacedPollingEmitter(PollingEmitter):
    """A poller that rests in proportion to what its last walk cost.

    watchdog waits for the interval and then walks, so the interval is a gap between passes rather
    than a period, and when the walk is the expensive part, the gap is all that stands between the
    library and being read continuously. Nothing in watchdog notices that.

    So each pass is timed, and the next wait is whatever keeps the walking down to `POLL_WALK_SHARE`
    of the time. The configured interval is a floor, never a ceiling: a small library polls exactly
    as often as it was told to, because its pass costs nothing and the floor always wins.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._rest_seconds = 0.0
        self._reported = False

    def queue_events(self, timeout: float) -> None:
        waited = min(MAX_POLL_REST_SECONDS, max(timeout, self._rest_seconds))
        began = time.monotonic()
        super().queue_events(waited)
        # What the walk cost, separated from the wait asked for. The base class does both inside
        # this one call, and a stopped watcher returns early, hence the floor at zero rather than
        # trusting the subtraction.
        walked = max(0.0, (time.monotonic() - began) - waited)
        self._rest_seconds = walked * (1.0 / POLL_WALK_SHARE - 1.0)
        if not self._reported and self._rest_seconds > timeout:
            # Once per watch, so a library that is simply expensive to walk says so instead of
            # looking like a watcher that has quietly stopped noticing things.
            self._reported = True
            log.info(
                "library.watch_paced",
                path=str(self.watch.path),
                walk_seconds=round(walked, 1),
                resting_seconds=round(min(MAX_POLL_REST_SECONDS, self._rest_seconds), 1),
            )


class _PacedPollingObserver(PollingObserver):
    """`PollingObserver`, with the emitter above in place of watchdog's."""

    def __init__(self, *, timeout: float) -> None:
        # Naming a different emitter class is the entire change, so this goes past
        # PollingObserver's own __init__, which exists only to name watchdog's.
        BaseObserver.__init__(self, _PacedPollingEmitter, timeout=timeout)


@dataclass(slots=True)
class _Pending:
    """A folder that has changed, the moment it may be scanned, and what changed in it.

    `paths` is what turns a notification into one `stat` instead of a walk. It is dropped (and
    the folder scanned whole instead) in the three cases where naming files cannot be right:

    * something MOVED ACROSS DIRECTORIES, which may be a folder being renamed underneath, and only
      a listing can tell that from a file going away;
    * more than `MOST_NAMED_PATHS` files arrived at once, where a walk of the folder is cheaper
      than a payload of ten thousand strings and a `stat` each; and
    * the folder these files were in has itself gone, which is the one that cannot be decided from
      the notification and is decided on the disk instead (see `_folder_went`).

    Once dropped it stays dropped for this burst. A folder that has already earned a full walk does
    not un-earn it because the next event was an ordinary arrival.
    """

    due: float
    paths: set[str] | None = None
    #: What each named file measured last time the folder came due, so a file that is still growing
    #: can be told from one that has finished. See `_still_arriving`.
    sizes: dict[str, tuple[int, int]] = field(default_factory=dict)

    def note(self, rel_path: str | None) -> None:
        if self.paths is None:
            return
        if rel_path is None:
            self.paths = None
            return
        self.paths.add(rel_path)
        if len(self.paths) > MOST_NAMED_PATHS:
            self.paths = None


#: The least time between two scans of the SAME folder, however much it keeps changing.
#:
#: The settle-wait is not a bound on its own. It bounds one burst; it says nothing about how often
#: bursts arrive. On a filesystem whose events never really stop (a network share, or a Windows
#: drive seen through WSL, where routine activity reads as constant change), the folder goes quiet
#: for the settle-wait over and over, and each time is another scan. Those are also the filesystems
#: a scan is slowest on, so the scans overlap: a folder that takes a minute to walk, handed to a
#: scan every few seconds, ends up being walked by every worker at once and nothing else in the
#: queue ever runs. Deduping the queue alone does not fix it, because each new scan is allowed the
#: moment the last one is claimed.
#:
#: A change made during the cooldown is not lost. The folder is re-armed for the moment the cooldown
#: ends, so it is scanned once more, late, rather than repeatedly or never.
SCAN_COOLDOWN_SECONDS = 60.0

#: The events that mean a file's CONTENT changed, and therefore the only ones worth a scan.
#:
#: Reading a file is not a change to it, and treating it as one is a loop that feeds itself: a scan
#: opens and reads every file in the folder, each read raises `opened` and `closed_no_write`, those
#: schedule another scan, and that scan reads every file again: a scan finishing and the next one
#: triggered seconds later, for a folder nobody has touched in hours. It never stops on its own, and
#: every pass re-reads the whole library.
#:
#: `closed` (a close after WRITING) is kept and is the most reliable signal there is that a
#: download or a copy has actually finished. Its read-only twin, `closed_no_write`, is what a scan
#: produces, and is exactly what must not come back here.
WATCHED_EVENTS = frozenset(
    {
        EVENT_TYPE_CREATED,
        EVENT_TYPE_MODIFIED,
        EVENT_TYPE_MOVED,
        EVENT_TYPE_DELETED,
        EVENT_TYPE_CLOSED,
    }
)


#: The events that name ONE FILE and say nothing about the shape of the library around it.
#:
#: A deletion is here. See `_can_be_named` for the argument that puts it on this side and for the
#: one question it leaves open.
NAMEABLE = frozenset(
    {EVENT_TYPE_CREATED, EVENT_TYPE_MODIFIED, EVENT_TYPE_CLOSED, EVENT_TYPE_DELETED}
)


def _can_be_named(event: FileSystemEvent) -> bool:
    """Whether this change can be answered by looking at the named file, or needs the folder walked.

    A file that arrived, was written, or went away can be taken in by naming it: a `stat` instead of
    a walk of everything around it. What cannot is a change to the SHAPE of the library: a folder
    renamed arrives as a moved event for every file inside it, and what has to happen then is that
    the folder's ROW moves, keeping its id and its shares. That is a comparison of listings
    (`_reconcile_folders`), not something a list of files can express.

    **A DELETION IS NAMED.** The half of a scan that decides a file has gone is the sweep, and a
    sweep of a folder somebody RENAMED has to see the whole listing to tell "moved" from "gone",
    but that is about the FOLDER going, not about the file. A file deleted out of a folder that is
    still there is exactly as nameable as one that arrived in it: `look_at` leaves out a path that
    is not on the disk, and the narrowed sweep marks it missing. The catch-up pass has always relied
    on precisely that (`_differences` names what Sift believes is present and the listing does not
    show).

    What the folder case is really about is a question no notification can answer, because
    watchdog raises a plain file-deleted event for a DIRECTORY too: by the time it arrives there
    is nothing left to ask what it was. So that question is asked of the disk, once per burst,
    where it can actually be answered: see `_folder_went`.

    Walked instead, deleting one file from a watched folder would answer with `named: null` and a
    walk of thousands of files, and Sift's own delete does that to itself.

    **A RENAME WITHIN ONE DIRECTORY IS AN ARRIVAL.** Pasting a file into a folder IS a move:
    Explorer and every downloader write under one name and rename into place, so one pasted file
    produces `created`, `modified` and then `moved`. Treated as structural, the moved would drop
    the named paths and walk thousands of files to take in the one that had already been named
    twice.

    The test is where the two ends are. A file renamed inside one directory has the same parent at
    both ends and nothing about the library's shape has changed: both ends are named, because the
    old name has gone and the new one has arrived, and the filtered sweep needs to hear about both.
    A move that CROSSES directories is the folder case, and is walked.

    """
    if event.event_type in NAMEABLE:
        return True
    if event.event_type != EVENT_TYPE_MOVED:
        return False
    destination = getattr(event, "dest_path", None)
    if not destination:
        # A move with no destination is the file leaving, and where it went is not this watch's to
        # say. Walked.
        return False
    if not event.src_path:
        # A NAME APPEARED AND NOTHING SAYS WHERE IT CAME FROM, which is an arrival, and on a
        # network share it is the ordinary way a paste is reported.
        #
        # Windows sends a rename as two notifications, an old name and a new one, and watchdog pairs
        # them by remembering the old until the new arrives. Over SMB they do not always land in the
        # same read of the buffer, so the new one turns up with the old still unseen and watchdog
        # emits it with an EMPTY source: one paste can produce a properly paired move AND a second
        # move with no source at all.
        #
        # `Path("").parent` is `.`, so the same-directory test below would read that as a move
        # across directories (structural), and one pasted file would walk the whole library. A
        # paste into a LOCAL folder pairs perfectly, which is why only a share shows it.
        return True
    return Path(str(event.src_path)).parent == Path(str(destination)).parent


class _Events(FileSystemEventHandler):
    """Turns watchdog's callbacks into something the event loop can hear.

    Every method here runs on watchdog's own thread, not on the event loop, and that is the entire
    reason this class exists. Touching an asyncio structure from another thread is unsupported and
    fails rarely enough to reach production: `call_soon_threadsafe` is the one sanctioned way
    across, and it is what makes the rest of this file ordinary single-threaded code.
    """

    def __init__(
        self,
        root_id: str,
        loop: asyncio.AbstractEventLoop,
        notify: Callable[[str, Path, bool], None],
    ) -> None:
        self._root_id = root_id
        self._loop = loop
        self._notify = notify
        #: Cleared when the watcher lets go of the observer that owns this handler. An observer
        #: that will not stop is left running rather than waited on for ever (see
        #: `_stop_observers`), and a thread still delivering events into a loop that is closing is
        #: exactly what that trade must not cost. Read without a lock on purpose: a bool assigned
        #: once and never back is the one thing threads can share safely, and the worst a stale
        #: read can do is deliver one more event to a handler that is about to be dropped.
        self._live = True

    def detach(self) -> None:
        """Stop delivering. Anything still arriving after this is somebody else's problem."""
        self._live = False

    @property
    def live(self) -> bool:
        """Whether the watch this delivers for is still held. False once it has been let go of."""
        return self._live

    def on_any_event(self, event: FileSystemEvent) -> None:
        if not self._live:
            return
        # Reading is not changing. See WATCHED_EVENTS: without this the scan's own reads schedule
        # the next scan, forever.
        if event.event_type not in WATCHED_EVENTS:
            return
        if event.is_directory:
            # A directory event carries no file. The file events inside it are what matter, and a
            # directory that is created empty has nothing to scan yet.
            #
            # Renaming a folder is handled, and it is worth saying where: watchdog raises a moved
            # event for every FILE inside a directory that was renamed, not just for the directory,
            # so the branch below sees them and the folder's contents are re-indexed at their new
            # path. Adding the directory itself here changes nothing except how often a polling
            # watch asks for a scan.
            return
        for raw in (event.src_path, getattr(event, "dest_path", None)):
            # Both ends of a rename. A file renamed INTO a watched folder arrives only as a
            # destination (there is no create event for it), so watching src alone misses every
            # file that was assembled under a temporary name, which is how most downloaders write.
            if not raw:
                continue
            path = Path(str(raw))
            # The SAME list the walk stops for, archives included. `ALLOWED_EXTENSIONS` is "things
            # Sift can decode" and does not name a `.zip`, so with it a gallery dropped into a
            # watched folder would be ignored until somebody happened to rescan the whole root.
            #
            # Imported from the walk rather than rebuilt here, so the two cannot come to disagree
            # about what is worth looking at.
            if path.suffix.lower() not in WORTH_OPENING:
                continue
            self._loop.call_soon_threadsafe(self._notify, self._root_id, path, _can_be_named(event))


@dataclass(frozen=True, slots=True)
class _Look:
    """What one pass over a burst found on the disk.

    Two answers in one object because they are read at the same moment and cost the same handful of
    `stat` calls, not because they are one question. `sizes` says whether the named files have
    stopped changing; `folder_is_there` says whether naming them is the right answer at all.
    """

    #: Each named file that exists, by size and modification time. See `_Pending.sizes`.
    sizes: dict[str, tuple[int, int]]
    #: Whether the folder those files were in is still a directory on the disk.
    folder_is_there: bool


def _measure(base: Path, rel_dir: str, rel_paths: list[str]) -> _Look:
    """Every named file, and the folder holding them. Blocking; call it on a thread.

    A file that is not there is left out rather than recorded as zero, so it neither looks like a
    change every time nor like a file that has settled at nothing.

    The folder is asked about ONCE, however many files the burst named, and it is the only way to
    tell a few files being deleted from a whole folder going away: watchdog reports a directory's
    removal as an ordinary file deletion, because by the time the notification arrives there is
    nothing left to ask what it was.

    !! UNREADABLE IS NOT GONE.** Only the two errors that really mean "there is no directory at this
    path" count as absence. A permissions change, or a share that stopped answering, would otherwise
    turn every deletion into a walk of the folder above, which is the reading that costs the most
    and is available exactly when the disk is least able to pay for it.
    """
    seen: dict[str, tuple[int, int]] = {}
    for rel_path in rel_paths:
        try:
            stat = (base / rel_path).stat()
        except OSError:
            continue
        seen[rel_path] = (stat.st_size, stat.st_mtime_ns)
    here = base / rel_dir if rel_dir else base
    try:
        folder_is_there = stat_module.S_ISDIR(os.stat(here).st_mode)
    except NotADirectoryError:
        folder_is_there = False
    except OSError as error:
        # A share that stopped answering raises the same class as a folder that is gone; the
        # code on the error is what tells them apart, and the root is asked where the code alone
        # cannot say. See `is_absence`.
        folder_is_there = not is_absence(error, under=base)
    return _Look(sizes=seen, folder_is_there=folder_is_there)


class LibraryWatcher:
    """Watches every root that asked to be watched, and scans a folder once it goes quiet.

    One per application, started and stopped with it.
    """

    def __init__(
        self,
        library: LibraryStore,
        queue: JobQueue,
        *,
        quiet_seconds: float = QUIET_SECONDS,
        tick_seconds: float = TICK_SECONDS,
        cooldown_seconds: float = SCAN_COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        reattach_seconds: float = REATTACH_FIRST_SECONDS,
    ) -> None:
        self._library = library
        self._queue = queue
        self._quiet = quiet_seconds
        self._tick = tick_seconds
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._observers: list[BaseObserver] = []
        #: The handler each observer delivers through, kept beside it so it can be switched
        #: off when the observer is let go (see `_stop_observers`).
        self._handlers: list[_Events] = []
        #: Where each watched root is on disk, so an event's path can be turned back into a folder.
        #: Kept here rather than read back out of the observer: watchdog's own record of it is
        #: private, and a library that stops watching on a version bump would do so in silence.
        self._bases: dict[str, Path] = {}
        #: The folder a file landed in, the moment it may be scanned, and which files changed. A
        #: later event on the same folder pushes the moment out, which is what makes this a
        #: settle-wait rather than a timer.
        self._pending: dict[tuple[str, str], _Pending] = {}
        #: When each folder was last handed to a scan, so one that will not stop changing cannot be
        #: scanned faster than the cooldown allows.
        self._last_scan: dict[tuple[str, str], float] = {}
        self._task: asyncio.Task[None] | None = None
        #: The in-flight attach, so shutting down does not leave a folder walk running behind it.
        self._attaching: asyncio.Task[None] | None = None
        #: Which observer and handler watch each root, so ONE watch that ended can be let go of and
        #: replaced without touching the others. The two lists above are the same watches, kept for
        #: the letting-go of all of them at once.
        self._watches: dict[str, tuple[BaseObserver, _Events]] = {}
        #: A root whose watch ended and is being attached again, by the task doing it.
        self._reattaching: dict[str, asyncio.Task[None]] = {}
        self._reattach_seconds = reattach_seconds

    async def start(self) -> None:
        """Begin watching every watched root, without holding up whatever is starting us.

        Attaching a watcher walks the folder tree, and on a slow or very large one (a network
        share, or a Windows drive seen through WSL), that walk takes minutes. Awaited during
        startup, it would keep Sift from finishing starting and serving anything at all: one slow
        folder and the whole application unreachable, with no error to explain it. So the observers
        are attached in the background and the app comes up regardless. Roots added later are
        picked up by `refresh`.
        """
        self._task = asyncio.create_task(self._run())
        self._attaching = asyncio.create_task(self.refresh())

    async def watching(self) -> None:
        """Wait until the observers are actually attached.

        Startup deliberately does not wait (see `start`), but anything that means "watching has
        begun by the time this returns" needs a way to say so, and a test that drops a file the
        instant after starting is exactly that. Without it the file lands before inotify is
        listening and the test is a race it usually loses.
        """
        if self._attaching is not None:
            await self._attaching

    async def refresh(self) -> None:
        """Match the observers to the roots that currently want watching.

        Called at boot and whenever a root is added or removed.
        Simplest correct thing: stop everything and start what should be running. Reconciling the
        difference would be an optimisation over an operation that happens when a person clicks a
        toggle.
        """
        # Every attach in flight first: this attaches every root itself, and a re-attach finishing
        # after the stop below would hand a root a second watch.
        await self._cancel_reattaching()
        await asyncio.to_thread(self._stop_observers)
        loop = asyncio.get_running_loop()
        for root in await self._library.roots():
            # Off the event loop: attaching walks the tree, and a slow share would otherwise stall
            # every request being served while it did.
            watching = await asyncio.to_thread(self._observe, root, loop)
            if watching is not None:
                self._keep(root, watching)
            # AFTER the watch is attached, and for every root whether or not it could be.
            #
            # A watcher reports changes, and a change is only a change relative to the moment it
            # started watching, so anything that arrived while Sift was closed is not a change and
            # never becomes one. This is what finds those, and it must be queued after attaching or
            # a file landing in the gap between the two belongs to neither.
            #
            # Queued rather than done here: it reads a disk, and `refresh` runs during startup. And
            # it is queued for a root whose watch FAILED as well, because the catch-up is the only
            # thing such a root will ever get.
            await self._catch_up(root.id)

    def _keep(self, root: Root, watching: tuple[BaseObserver, _Events]) -> None:
        """Record a watch that attached, under its root. On the loop."""
        observer, handler = watching
        self._observers.append(observer)
        self._handlers.append(handler)
        self._watches[root.id] = watching
        self._bases[root.id] = Path(root.abs_path)

    def _ended(self, root_id: str, handler: _Events, loop: asyncio.AbstractEventLoop) -> None:
        """A watch stopped by itself: the share went, the folder went. On the emitter's thread.

        It hops to the loop like every other notification here, and asks for the re-attach there.
        """
        loop.call_soon_threadsafe(self._watch_ended, root_id, handler)

    def _watch_ended(self, root_id: str, handler: _Events) -> None:
        """Begin attaching this root again, unless the watch that ended is no longer the root's.

        A handler that has been detached belongs to a watch that was LET GO (a refresh, a stop,
        a root removed), and one that is not the root's current handler was already replaced;
        either way this is the letting-go being heard late, and there is nothing to come back to.
        One re-attach per root at a time: a second ending while one is running is the same outage.
        """
        current = self._watches.get(root_id)
        if not handler.live or current is None or current[1] is not handler:
            return
        running = self._reattaching.get(root_id)
        if running is not None and not running.done():
            return
        self._reattaching[root_id] = asyncio.create_task(self._reattach(root_id))

    async def _reattach(self, root_id: str) -> None:
        """Attach a root whose watch ended, waiting longer each time it will not, then catch up.

        !! WITHOUT THIS A SHARE THAT DROPPED WOULD NEVER BE WATCHED AGAIN UNTIL SIFT RESTARTED.
        The native watch stops when its read fails, and with nothing asking for another, a file
        added to the share after it came back would appear only when somebody pressed Rescan or
        restarted.

        The watch that ended is let go of first, the ordinary bounded way. Then the root is asked
        for again after `reattach_seconds`, doubling up to `REATTACH_MOST_SECONDS` while it will not
        attach: a share that is still away answers every attempt the same, so asking faster buys
        nothing but timeouts. Once it attaches, the catch-up pass is asked for: whatever arrived
        while nobody was listening is not a change the new watch will ever report.

        A root removed meanwhile ends this quietly. A refresh or a stop cancels it (a refresh
        attaches every root itself), and a watch that attached while the cancel landed is let go
        of rather than left running with nobody holding it.
        """
        loop = asyncio.get_running_loop()
        dead = self._watches.pop(root_id, None)
        if dead is not None:
            observer, handler = dead
            with contextlib.suppress(ValueError):
                self._observers.remove(observer)
            with contextlib.suppress(ValueError):
                self._handlers.remove(handler)
            await asyncio.to_thread(_let_go, [observer], [handler])
        delay = self._reattach_seconds
        attempts = 0
        while True:
            await asyncio.sleep(delay)
            attempts += 1
            root = await self._library.get_root(root_id)
            if root is None:
                self._bases.pop(root_id, None)
                return
            attaching = asyncio.ensure_future(asyncio.to_thread(self._observe, root, loop))
            try:
                watching = await asyncio.shield(attaching)
            except asyncio.CancelledError:
                late = await attaching
                if late is not None:
                    await asyncio.to_thread(_let_go, [late[0]], [late[1]])
                raise
            if watching is not None:
                self._keep(root, watching)
                log.info("library.watch_reattached", root_id=root_id, attempts=attempts)
                await self._catch_up(root_id)
                return
            delay = min(delay * 2, REATTACH_MOST_SECONDS)

    async def _cancel_reattaching(self) -> None:
        """Stop every re-attach in flight, and wait for each to have let go of what it held."""
        running = list(self._reattaching.values())
        self._reattaching.clear()
        for task in running:
            task.cancel()
        for task in running:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _catch_up(self, root_id: str) -> None:
        """Ask for a pass over one library's folders, to find what moved while Sift was off.

        Deduped like the scans are: `refresh` runs at boot and again whenever a root is added,
        removed or toggled, and three of those in quick succession should be one pass rather than
        three reading the same disk at once.
        """
        # Switched off is not a failure here. This runs for the machine rather than for somebody,
        # so a refusal is the answer rather than something to report: the queue says so in its own
        # log, and a raise from inside a background task would be an unretrieved exception.
        try:
            await self._queue.enqueue(RECONCILE, {"root_id": root_id}, dedupe=True)
        except JobSwitchedOff:
            return

    def _observe(
        self, root: Root, loop: asyncio.AbstractEventLoop
    ) -> tuple[BaseObserver, _Events] | None:
        base = Path(root.abs_path)
        #
        # WHETHER A SHARE REPORTS ITS OWN CHANGES IS A QUESTION ABOUT THE PLATFORM, NOT ABOUT THE
        # ROOT, and the two answers are opposite.
        #
        # inotify is a kernel telling a program about its own filesystem, so a file written by
        # another machine onto a NAS never touches this kernel and a share reports nothing. That is
        # not what happens on **Windows**, which is the platform Sift ships on: there
        # `ReadDirectoryChangesW` on a mapped drive is served by the FILE SERVER, through SMB2
        # `CHANGE_NOTIFY`, which is a mandatory command in the protocol. A file created on a share
        # (by this machine or by another) is reported within milliseconds, and so is a deletion.
        # What remains is the fallback below, which polls a root whose native watch will not attach.
        polling = root.kind is RootKind.NAS and not _NATIVE_WATCH_IS_SAFE
        handler = _Events(root.id, loop, self._notice)
        ended = partial(self._ended, root.id, handler, loop)
        observer = self._observer_for(root, loop, polling=polling, ended=ended)
        try:
            observer.schedule(handler, str(base), recursive=True)
            observer.start()
        except OSError as exc:
            # A root on a drive that is not plugged in, or a watch limit the kernel will not raise.
            # Not fatal: the library is still browsable and a rescan still works by hand.
            #
            # A NATIVE watch that will not attach falls back to polling rather than giving up, and
            # that is the capability decision made where it can actually be observed: whether this
            # filesystem reports its own changes is not something a root's `kind` knows.
            if not polling:
                log.info(
                    "library.watch_falling_back_to_polling", root_id=root.id, error=exc.strerror
                )
                polling = True
                observer = self._observer_for(root, loop, polling=True, ended=ended)
                try:
                    observer.schedule(handler, str(base), recursive=True)
                    observer.start()
                except OSError as second:
                    log.warning("library.watch_failed", root_id=root.id, error=second.strerror)
                    return None
            else:
                log.warning("library.watch_failed", root_id=root.id, error=exc.strerror)
                return None
        log.info(
            "library.watching",
            root_id=root.id,
            kind=root.kind.value,
            polling=polling,
        )
        return observer, handler

    def _observer_for(
        self,
        root: Root,
        loop: asyncio.AbstractEventLoop,
        *,
        polling: bool,
        ended: Callable[[], None] | None = None,
    ) -> BaseObserver:
        """A poller, or the platform's own notifications.

        On Windows the native one is Sift's own emitter rather than watchdog's. See
        `native_watch.py` for the four calls that differ and why the library's version could not be
        used. Everywhere else it is watchdog's, unchanged.

        `ended` is how a native watch that stops by itself says so (see `_reattach`). A poller
        does not end: a folder that goes away is a walk that finds nothing, and the walk after the
        folder comes back finds it again.
        """
        if polling:
            return _PacedPollingObserver(timeout=DEFAULT_POLL_SECONDS)
        # Read from a name rather than testing `sys.platform` here, and that is not style: a type
        # checker narrows a direct `sys.platform` test to the platform it is configured for and then
        # reports the other branch as dead code, which it is, for that configuration, and is not
        # for the person running this on a Linux server.
        if _WINDOWS:
            # NOT the poll interval. For a poller that number is how often to look; for a native
            # watch nothing is being looked at, and the timeout is only how long the emitter waits
            # before checking whether it has been asked to stop. Handing it the poll interval would
            # tie one meaning to a setting about the other, and a person shortening their poll
            # interval would silently be asking a native watch to wake up more often for nothing.
            return SafeWindowsApiObserver(
                timeout=_STOP_CHECK_SECONDS,
                on_overflow=partial(self._overflowed, root.id, loop),
                on_ended=ended,
            )
        return Observer()

    def _overflowed(self, root_id: str, loop: asyncio.AbstractEventLoop) -> None:
        """The operating system had more changes than it could hold and threw some away.

        Runs on the emitter's thread, so it hops to the loop like every other notification here.
        What it asks for is the catch-up pass, which is exactly the right answer, because "some
        changes were lost" and "find what changed while nobody was listening" are the same question.
        Nothing new is needed for it.
        """
        loop.call_soon_threadsafe(lambda: asyncio.create_task(self._catch_up(root_id)))

    def _notice(self, root_id: str, path: Path, nameable: bool = True) -> None:
        """A file changed. Push this folder's deadline out, and remember which file. On the loop.

        `nameable` is `_can_be_named`'s answer: whether this change is about one file, or about the
        shape of the library around it. False drops the burst to a walk of the folder, for good.
        """
        try:
            rel_dir = self._folder_of(root_id, path)
            rel_path = self._path_of(root_id, path) if nameable else None
        except (LookupError, ValueError):
            # Outside the root, or a path that cannot be a library path. Nothing to scan.
            return
        key = (root_id, rel_dir)
        pending = self._pending.get(key)
        if pending is None:
            pending = _Pending(due=0.0, paths=set())
            self._pending[key] = pending
        pending.due = self._clock() + self._quiet
        pending.note(rel_path)

    def _path_of(self, root_id: str, path: Path) -> str:
        """The changed file, relative to its root, as a library path.

        Through `check_rel_path` like every stored path, so a name the library could never hold
        cannot be handed to a job, and so the refusal happens here, on the loop, rather than in a
        worker reading a payload somebody else built.
        """
        base = self._bases.get(root_id)
        if base is None:
            raise LookupError(f"no watched root {root_id}")
        return check_rel_path(str(path.relative_to(base).as_posix()))

    def _folder_of(self, root_id: str, path: Path) -> str:
        """The folder a changed file sits in, relative to its root.

        `relative_to` raises for a path outside the root, which is the check as well as the answer:
        a symlink or a stray event cannot name a folder in a library it is not in.
        """
        base = self._bases.get(root_id)
        if base is None:
            raise LookupError(f"no watched root {root_id}")
        rel_dir = str(path.parent.relative_to(base).as_posix())
        return "" if rel_dir == "." else check_rel_path(rel_dir)

    async def _run(self) -> None:
        """Hand every folder that has gone quiet to a scan."""
        while True:
            try:
                await asyncio.sleep(self._tick)
                await self._sweep_pending()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A watcher that dies takes every watched folder with it, silently, until Sift is
                # restarted. Whatever went wrong with one folder, the loop keeps running.
                log.error("library.watch_loop_error", error=str(exc))

    async def _sweep_pending(self) -> None:
        now = self._clock()
        ready = [key for key, pending in self._pending.items() if pending.due <= now]
        for key in ready:
            pending = self._pending[key]
            last = self._last_scan.get(key)
            # THE COOLDOWN IS A RATE LIMIT ON WALKS, and only on walks.
            #
            # It exists because a filesystem whose events never really settle would otherwise queue
            # a scan of the same folder every few seconds, and a scan is a whole worker walking a
            # whole subtree, so the pool fills with re-scans of one folder and nothing else runs.
            # Every word of that is about the cost of a WALK.
            #
            # A named scan is not a walk. It is a `stat` per file, capped at `MOST_NAMED_PATHS`, and
            # holding it behind a minute-long rate limit does not protect anything: it just makes
            # a file that arrived take a minute to appear, waiting out the cooldown left over from
            # a walk the paste itself triggered.
            #
            # What bounds a named scan instead is the settle-wait in front of it, which already
            # collapses a burst into one, and the cap, which turns anything bigger into a walk,
            # and a walk is what the cooldown then applies to.
            if pending.paths is None and last is not None and now - last < self._cooldown:
                # Too soon after the last one. Re-armed for the moment the cooldown ends rather
                # than dropped, so whatever changed during it is still scanned (once, late)
                # instead of either being lost or starting another walk on top of the last.
                #
                # The paths collected so far are KEPT across the wait rather than reset, which is
                # what stops the cooldown turning a named scan into a walk: everything that arrived
                # during it is named in the one scan that follows.
                pending.due = last + self._cooldown
                continue
            root_id, rel_dir = key
            # ONE PASS OVER THE DISK, answering both of the questions a named burst leaves open.
            # None when there is nothing to ask: a walk has no path list, and needs neither.
            look = await self._look_at(root_id, rel_dir, pending)
            # IS THE FOLDER STILL THERE? Asked before the settle question and not after it, because
            # a folder that has gone makes every file in it measure as absent, which reads as a
            # burst that is still changing, and would spend a re-check to arrive here anyway.
            if look is not None and not look.folder_is_there:
                self._folder_went(key, now)
                continue
            # STILL BEING WRITTEN? Then it has not really settled, whatever the notifications say.
            #
            # The settle-wait alone would be enough if every folder were POLLED, because a poller
            # sees a file grow on every pass. It is not enough with the platform's own
            # notifications: NTFS defers a file's size and timestamp while it is held open, so
            # Windows reports the create and then, for a writer that keeps the handle, may report
            # nothing at all for seconds. The folder looks quiet and the file is half there.
            #
            # That matters: a half-written file hashes perfectly happily and the digest is of the
            # half: an asset that is wrong for ever, which
            # nothing revisits, because as far as Sift is concerned it was indexed. So the file
            # itself is asked, rather than inferred from the silence.
            if look is not None and look.sizes != pending.sizes:
                pending.sizes = look.sizes
                # A SHORT re-check, not another full settle-wait.
                #
                # The two are different questions, and one number for both would cost every arriving
                # file twice the wait it needed: the settle-wait is the coarse signal that
                # a burst has stopped, and this is a confirmation that the bytes have stopped too.
                # Once the folder has already been quiet for a settle-wait, half a second of a file
                # not changing size is what is left to establish, which roughly halves the time a
                # file landing on a share takes end to end.
                #
                # Never longer than the settle-wait itself, so a test that shortens one shortens
                # both and the arithmetic stays the same at every scale.
                pending.due = now + min(_SETTLE_RECHECK_SECONDS, self._quiet)
                continue
            del self._pending[key]
            self._last_scan[key] = now
            await self._scan(root_id, rel_dir, pending.paths)

    async def _look_at(self, root_id: str, rel_dir: str, pending: _Pending) -> _Look | None:
        """Ask the disk about a NAMED burst, off the loop. None when there is nothing to ask.

        Only for a named burst, and that is a decision rather than an oversight. A folder scan has
        no path list to compare, and the walk it runs already checks every file against what was
        recorded, so there is nothing here it could add and a whole directory of `stat` calls it
        would cost. It has no use for the folder question either: a walk of a directory that is not
        there finds nothing and concludes nothing, which is the right thing to do.

        On a thread, because it is up to `MOST_NAMED_PATHS` `stat` calls plus one, and a library
        sits on a share as often as on a disk.
        """
        base = self._bases.get(root_id)
        if base is None or not pending.paths:
            return None
        return await asyncio.to_thread(_measure, base, rel_dir, sorted(pending.paths))

    def _folder_went(self, key: tuple[str, str], now: float) -> None:
        """The folder these files were in is gone, so what really changed is the folder ABOVE it.

        A folder can disappear two ways and they need opposite answers: removed, and its rows should
        go; renamed, and its row should MOVE, keeping its id, its shares and everything anybody
        recorded about it. Nothing here can tell those apart, and neither can the notifications:
        only the parent's own listing can, which is what `_reconcile_folders` compares. So the burst
        stops naming files and becomes a walk of the folder above.

        This is the same answer the catch-up pass already reaches for the same question: a folder
        whose directory will not `stat` comes back from `_folders_that_moved` as a walk of its
        parent, and for the reason written there.

        Re-armed rather than scanned here, so the walk goes through every rule the ordinary path
        applies: the cooldown that stops one folder being walked over and over, and the settle
        wait. Both of those belong to the parent's key, and neither was ever consulted for this one.
        """
        root_id, rel_dir = key
        del self._pending[key]
        if rel_dir == "":
            # The ROOT's own directory: a drive that is not plugged in, or a share that went away.
            # There is nothing above it to list, and nothing here can tell "unmounted" from
            # "deleted", so the library is left exactly as it is. Marking half a million files
            # missing because a USB disk was pulled out is the failure this refuses.
            log.info("library.watch_root_missing", root_id=root_id)
            return
        above = str(PurePosixPath(rel_dir).parent)
        parent = (root_id, "" if above == "." else above)
        waiting = self._pending.get(parent)
        if waiting is None:
            # Due at once: the settle-wait has already been served, down here, by the burst that
            # found this out.
            self._pending[parent] = _Pending(due=now)
        else:
            # Something is still happening up there. Its deadline stands (this must not cut a
            # settle-wait short), and `note(None)` is what turns it into the walk that is now
            # needed, whatever it was going to be.
            waiting.note(None)
        log.info("library.watch_folder_went", root_id=root_id)

    async def _scan(self, root_id: str, rel_dir: str, paths: set[str] | None = None) -> None:
        """Enqueue a scan of what changed: the named files, or the folder they sit in."""
        root = await self._library.get_root(root_id)
        if root is None:
            return
        try:
            folder = (
                await self._library.root_folder(root_id)
                if rel_dir == ""
                else await self._library.upsert_folder(root_id, rel_dir)
            )
        except OSError:
            return
        if folder is None:
            return
        # The payload carries the folder's id, never its path.
        #
        # Deduped, because this is the one enqueue in the app driven by something outside it. A
        # filesystem whose events never settle (a network share, or a Windows drive seen through
        # WSL, where every poll looks like a change) otherwise queues a scan of the same folder
        # every few seconds for as long as Sift runs, and since a scan is a worker each, the pool
        # fills with re-scans of one folder and nothing else in the queue ever runs.
        #
        # !! THE DEDUPE AND THE PATHS INTERACT, and the order matters. Dedupe is on the whole
        # payload, so two scans naming different files are two different jobs and both run, which
        # is right. What it does not collapse is two scans of the same folder naming different
        # files, and that is also right: each names work the other does not do.
        #
        # !! AND A WALK OF THE ROOT'S OWN TOP FOLDER IS THE WHOLE ROOT, which is why this goes
        # through `scan_shape` rather than spelling the payload out: a press names no folder at all,
        # and the dedupe matches the payload exactly, so naming the top folder's id here would
        # give the same walk two spellings that never collide, and one root would be walked twice
        # at once, for one answer.
        payload: dict[str, object] = scan_shape(root_id, folder)
        if paths:
            payload["paths"] = sorted(paths)
        try:
            await self._queue.enqueue(SCAN, payload, dedupe=True)
        # Switched off. Caught here rather than left to the sweep loop's catch-all, which logs at
        # ERROR and abandons the rest of the folders that were ready in the same tick.
        except JobSwitchedOff:
            return
        log.info(
            "library.watch_triggered_scan",
            root_id=root_id,
            folder_id=folder.id,
            named=len(paths) if paths else None,
        )

    def _stop_observers(self) -> None:
        """Let go of every observer, and never wait on one for ever.

        `Observer.stop()` LOOKS instant and is not bounded at all. It joins its own emitter threads
        inside itself with no timeout, and on Windows an emitter is parked in a directory-change
        call against a handle that may belong to a folder which has just been deleted, which is
        the ordinary case here, because this runs the moment a root is removed or a share goes
        away. Such a stop can never return, so a bounded join after it would never run, and the
        application's own shutdown would wait on it for ever. That shutdown is what closes the
        database and folds the write-ahead log, so a watcher that will not let go holds the one
        thing that must always finish.

        So the asking is done on a thread of its own and abandoned if it will not answer. What is
        left behind is a daemon thread with nothing to deliver to (the handlers are switched off
        first, before anything is asked to stop), and daemon threads do not hold a process open.
        """
        watching = list(self._observers)
        handlers = list(self._handlers)
        self._observers.clear()
        self._handlers.clear()
        self._watches.clear()
        self._bases.clear()
        _let_go(watching, handlers)

    async def stop(self) -> None:
        # The attach first: it is the one that may still be walking a slow tree, and cancelling the
        # sweep while an observer is still being handed to it leaves the observer running with
        # nothing left to deliver to.
        if self._attaching is not None:
            self._attaching.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._attaching
            self._attaching = None
        await self._cancel_reattaching()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await asyncio.to_thread(self._stop_observers)


def _let_go(watching: list[BaseObserver], handlers: list[_Events]) -> None:
    """Let go of these observers, and never wait on one for ever. Blocking; call it on a thread.

    See `LibraryWatcher._stop_observers` for why the asking is done on a thread of its own and
    abandoned if it will not answer. The one place that knows, for all of them at once and for the
    one watch a re-attach replaces.
    """
    if not watching:
        return

    # FIRST, and not as part of the stopping: whatever happens below, nothing that is still
    # running may reach the event loop again.
    for handler in handlers:
        handler.detach()

    def _ask() -> None:
        for observer in watching:
            # A stop that raises is a stop: there is nothing to be done about an observer that
            # cannot be shut down, and letting it through here would skip the ones after it.
            with contextlib.suppress(Exception):
                observer.stop()
        for observer in watching:
            observer.join(timeout=_STOP_SECONDS)

    asking = threading.Thread(target=_ask, name="library-watch-stop", daemon=True)
    asking.start()
    asking.join(_STOP_SECONDS * 2)
    if asking.is_alive():
        log.warning("library.watch_stop_abandoned", observers=len(watching))


#: Watched folders.
WATCHER: Part[LibraryWatcher] = Part("watcher")
