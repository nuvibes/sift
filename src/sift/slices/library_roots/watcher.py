# SPDX-License-Identifier: AGPL-3.0-or-later
"""Noticing that a file arrived, and waiting until it has finished arriving.
Not a job: it runs as long as Sift does. It decides when a folder is quiet; a scan does the rest."""

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
    # Guarded: native_watch uses kernel32 through ctypes, and this must import on Linux.
    from sift.slices.library_roots.native_watch import SafeWindowsApiObserver

log = get_logger(__name__)

#: Long enough that a file being copied keeps resetting it, short enough to feel immediate.
QUIET_SECONDS = 3.0

#: The resolution of the quiet period, not the period.
TICK_SECONDS = 0.5

#: When nothing configures one; watchdog's own ten seconds is too long to wait.
DEFAULT_POLL_SECONDS = 5.0

#: How long one observer gets to let go before it is left to the process.
_STOP_SECONDS = 5.0


#: The most of its time the poller may spend walking, so a slow tree backs off instead of walking
#: nonstop.
POLL_WALK_SHARE = 0.25

#: The share has no ceiling; past five minutes a watcher looks stopped.
MAX_POLL_REST_SECONDS = 300.0

#: True because `native_watch.py` issues the read OVERLAPPED and closes the handle on its own thread,
#: so no I/O is outstanding at close; watchdog's version could hang or crash on stop.
_NATIVE_WATCH_IS_SAFE = True

#: Backoff for re-attaching an ended watch; doubling reaches the ceiling in about five minutes.
REATTACH_FIRST_SECONDS = 5.0
REATTACH_MOST_SECONDS = MAX_POLL_REST_SECONDS

#: Much shorter than the settle-wait: the folder is already quiet, only the bytes are left to
#: confirm.
_SETTLE_RECHECK_SECONDS = 0.5

#: How long a native watch sleeps between checks for a stop; it does not affect noticing.
_STOP_CHECK_SECONDS = 1.0

_WINDOWS = sys.platform == "win32"


class _PacedPollingEmitter(PollingEmitter):
    """A poller that rests in proportion to what its last walk cost; the configured interval is a
    floor."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._rest_seconds = 0.0
        self._reported = False

    def queue_events(self, timeout: float) -> None:
        waited = min(MAX_POLL_REST_SECONDS, max(timeout, self._rest_seconds))
        began = time.monotonic()
        super().queue_events(waited)
        # The base class waits and walks in one call; a stopped watcher returns early.
        walked = max(0.0, (time.monotonic() - began) - waited)
        self._rest_seconds = walked * (1.0 / POLL_WALK_SHARE - 1.0)
        if not self._reported and self._rest_seconds > timeout:
            # Once per watch, so an expensive library says so rather than looking stopped.
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
        # Bypasses PollingObserver's __init__, which only names watchdog's emitter.
        BaseObserver.__init__(self, _PacedPollingEmitter, timeout=timeout)


@dataclass(slots=True)
class _Pending:
    """A folder that changed, when it may be scanned, and which files. `paths` is None for a walk:
    a move across directories, too many files, or the folder itself gone. Once dropped, it stays
    dropped."""

    due: float
    paths: set[str] | None = None
    #: What each named file measured last time, to tell growing from finished.
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


#: The least time between two scans of one folder: some filesystems never stop reporting change.
#: A change during the cooldown re-arms the folder for when it ends.
SCAN_COOLDOWN_SECONDS = 60.0

#: Events that mean CONTENT changed. Reads are left out, or a scan's own reads schedule the next
#: scan.
WATCHED_EVENTS = frozenset(
    {
        EVENT_TYPE_CREATED,
        EVENT_TYPE_MODIFIED,
        EVENT_TYPE_MOVED,
        EVENT_TYPE_DELETED,
        EVENT_TYPE_CLOSED,
    }
)


#: Events that name one file and say nothing about the library's shape.
NAMEABLE = frozenset(
    {EVENT_TYPE_CREATED, EVENT_TYPE_MODIFIED, EVENT_TYPE_CLOSED, EVENT_TYPE_DELETED}
)


def _can_be_named(event: FileSystemEvent) -> bool:
    """Whether this change can be answered by looking at the named file, or needs the folder walked.
    A deletion and a rename within one directory are nameable; a move across directories is
    walked."""
    if event.event_type in NAMEABLE:
        return True
    if event.event_type != EVENT_TYPE_MOVED:
        return False
    destination = getattr(event, "dest_path", None)
    if not destination:
        # A move with no destination: walked.
        return False
    if not event.src_path:
        # An SMB rename can arrive unpaired with an empty source; it is an arrival.
        return True
    return Path(str(event.src_path)).parent == Path(str(destination)).parent


class _Events(FileSystemEventHandler):
    """Turns watchdog's callbacks, on its own thread, into calls on the event loop."""

    def __init__(
        self,
        root_id: str,
        loop: asyncio.AbstractEventLoop,
        notify: Callable[[str, Path, bool], None],
    ) -> None:
        self._root_id = root_id
        self._loop = loop
        self._notify = notify
        #: Cleared when the watcher lets go of the observer, so an abandoned thread delivers nothing.
        self._live = True

    def detach(self) -> None:
        """Stop delivering."""
        self._live = False

    @property
    def live(self) -> bool:
        """Whether the watch this delivers for is still held."""
        return self._live

    def on_any_event(self, event: FileSystemEvent) -> None:
        if not self._live:
            return
        # Reading is not changing. See WATCHED_EVENTS.
        if event.event_type not in WATCHED_EVENTS:
            return
        if event.is_directory:
            # Directory events carry no file; a renamed folder raises a moved event for each file in
            # it.
            return
        for raw in (event.src_path, getattr(event, "dest_path", None)):
            # Both ends: a file renamed into the folder arrives only as a destination.
            if not raw:
                continue
            path = Path(str(raw))
            # The walk's own list, archives included.
            if path.suffix.lower() not in WORTH_OPENING:
                continue
            self._loop.call_soon_threadsafe(self._notify, self._root_id, path, _can_be_named(event))


@dataclass(frozen=True, slots=True)
class _Look:
    """What one pass over a burst found: whether files settled, and whether the folder is still
    there."""

    sizes: dict[str, tuple[int, int]]
    folder_is_there: bool


def _measure(base: Path, rel_dir: str, rel_paths: list[str]) -> _Look:
    """Every named file, and the folder holding them. Blocking; call it on a thread.
    Only a real absence counts as gone: unreadable is not gone."""
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
        # A dropped share raises the same class as a missing folder; see `is_absence`.
        folder_is_there = not is_absence(error, under=base)
    return _Look(sizes=seen, folder_is_there=folder_is_there)


class LibraryWatcher:
    """Watches every root that asked to be watched, and scans a folder once it goes quiet."""

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
        #: Kept so each can be switched off when its observer is let go.
        self._handlers: list[_Events] = []
        #: Kept here: watchdog's own record of it is private.
        self._bases: dict[str, Path] = {}
        #: A later event pushes the moment out, which makes this a settle-wait.
        self._pending: dict[tuple[str, str], _Pending] = {}
        self._last_scan: dict[tuple[str, str], float] = {}
        self._task: asyncio.Task[None] | None = None
        self._attaching: asyncio.Task[None] | None = None
        #: Per root, so one ended watch can be replaced alone.
        self._watches: dict[str, tuple[BaseObserver, _Events]] = {}
        self._reattaching: dict[str, asyncio.Task[None]] = {}
        self._reattach_seconds = reattach_seconds
        self._known: set[str] | None = None
        self._matching = asyncio.Lock()

    async def start(self) -> None:
        """Begin watching every root in the background; attaching can walk a slow share for minutes."""
        self._task = asyncio.create_task(self._run())
        self._attaching = asyncio.create_task(self.refresh())

    async def watching(self) -> None:
        """Wait until the observers are attached, which `start` does not."""
        if self._attaching is not None:
            await self._attaching

    async def refresh(self) -> None:
        """Match the watches to the library folders: drop the removed and moved, attach the rest."""
        async with self._matching:
            first = self._known is None
            roots = {root.id: root for root in await self._library.roots()}
            moved = {
                root_id
                for root_id, base in self._bases.items()
                if root_id in roots and Path(roots[root_id].abs_path) != base
            }
            for root_id in [one for one in self._watches if one not in roots or one in moved]:
                await self._drop(root_id)
            for root_id in [one for one in self._reattaching if one not in roots or one in moved]:
                task = self._reattaching.pop(root_id)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            for root_id in [one for one in self._bases if one not in roots or one in moved]:
                del self._bases[root_id]
            loop = asyncio.get_running_loop()
            for root in roots.values():
                running = self._reattaching.get(root.id)
                if root.id in self._watches or (running is not None and not running.done()):
                    continue
                watching = await asyncio.to_thread(self._observe, root, loop)
                if watching is not None:
                    self._keep(root, watching)
                # After attaching, so a file landing between the two belongs to one of them.
                if first or (root.id in (self._known or set()) and root.id not in moved):
                    await self._catch_up(root.id)
            self._known = set(roots)

    def _keep(self, root: Root, watching: tuple[BaseObserver, _Events]) -> None:
        """Record a watch that attached, under its root. On the loop."""
        observer, handler = watching
        self._observers.append(observer)
        self._handlers.append(handler)
        self._watches[root.id] = watching
        self._bases[root.id] = Path(root.abs_path)

    async def _drop(self, root_id: str) -> None:
        """Let go of one root's watch, if it has one."""
        dead = self._watches.pop(root_id, None)
        if dead is not None:
            observer, handler = dead
            with contextlib.suppress(ValueError):
                self._observers.remove(observer)
            with contextlib.suppress(ValueError):
                self._handlers.remove(handler)
            await asyncio.to_thread(_let_go, [observer], [handler])

    def _ended(self, root_id: str, handler: _Events, loop: asyncio.AbstractEventLoop) -> None:
        """A watch stopped by itself (the share or folder went). On the emitter's thread."""
        loop.call_soon_threadsafe(self._watch_ended, root_id, handler)

    def _watch_ended(self, root_id: str, handler: _Events) -> None:
        """Begin attaching this root again, unless the ended watch was already let go of or
        replaced."""
        current = self._watches.get(root_id)
        if not handler.live or current is None or current[1] is not handler:
            return
        running = self._reattaching.get(root_id)
        if running is not None and not running.done():
            return
        self._reattaching[root_id] = asyncio.create_task(self._reattach(root_id))

    async def _reattach(self, root_id: str) -> None:
        """Attach a root whose watch ended, with doubling backoff, then catch up on what arrived
        meanwhile."""
        loop = asyncio.get_running_loop()
        await self._drop(root_id)
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
        """Ask for a pass over one library's folders, to find what moved while Sift was off."""
        # Switched off is the answer here, not a failure to report.
        try:
            await self._queue.enqueue(RECONCILE, {"root_id": root_id}, dedupe=True)
        except JobSwitchedOff:
            return

    def _observe(
        self, root: Root, loop: asyncio.AbstractEventLoop
    ) -> tuple[BaseObserver, _Events] | None:
        base = Path(root.abs_path)
        # On Windows a share reports its own changes through SMB2 CHANGE_NOTIFY; polling is the
        # fallback.
        polling = root.kind is RootKind.NAS and not _NATIVE_WATCH_IS_SAFE
        handler = _Events(root.id, loop, self._notice)
        ended = partial(self._ended, root.id, handler, loop)
        observer = self._observer_for(root, loop, polling=polling, ended=ended)
        try:
            observer.schedule(handler, str(base), recursive=True)
            observer.start()
        except OSError as exc:
            # A drive not plugged in or a watch limit: a native watch that will not attach falls
            # back to polling.
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
        """A poller, or the platform's own notifications (Sift's own emitter on Windows)."""
        if polling:
            return _PacedPollingObserver(timeout=DEFAULT_POLL_SECONDS)
        # A name, so a type checker does not report the other platform's branch as dead.
        if _WINDOWS:
            # Not the poll interval: for a native watch this is only how often it checks for a stop.
            return SafeWindowsApiObserver(
                timeout=_STOP_CHECK_SECONDS,
                on_overflow=partial(self._overflowed, root.id, loop),
                on_ended=ended,
            )
        return Observer()

    def _overflowed(self, root_id: str, loop: asyncio.AbstractEventLoop) -> None:
        """The OS dropped changes; the catch-up pass is the answer. On the emitter's thread."""
        loop.call_soon_threadsafe(lambda: asyncio.create_task(self._catch_up(root_id)))

    def _notice(self, root_id: str, path: Path, nameable: bool = True) -> None:
        """A file changed: push this folder's deadline out, and remember which file. On the loop."""
        try:
            rel_dir = self._folder_of(root_id, path)
            rel_path = self._path_of(root_id, path) if nameable else None
        except (LookupError, ValueError):
            return
        key = (root_id, rel_dir)
        pending = self._pending.get(key)
        if pending is None:
            pending = _Pending(due=0.0, paths=set())
            self._pending[key] = pending
        pending.due = self._clock() + self._quiet
        pending.note(rel_path)

    def _path_of(self, root_id: str, path: Path) -> str:
        """The changed file, relative to its root, checked as a library path."""
        base = self._bases.get(root_id)
        if base is None:
            raise LookupError(f"no watched root {root_id}")
        return check_rel_path(str(path.relative_to(base).as_posix()))

    def _folder_of(self, root_id: str, path: Path) -> str:
        """The folder a changed file sits in, relative to its root; outside the root raises."""
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
                # One folder's failure must not stop watching for every other.
                log.error("library.watch_loop_error", error=str(exc))

    async def _sweep_pending(self) -> None:
        now = self._clock()
        ready = [key for key, pending in self._pending.items() if pending.due <= now]
        for key in ready:
            pending = self._pending[key]
            last = self._last_scan.get(key)
            # The cooldown limits walks only; a named scan is a stat per file and is not held behind
            # it.
            if pending.paths is None and last is not None and now - last < self._cooldown:
                # Re-armed for when the cooldown ends, keeping the paths, so the change is scanned
                # once, late.
                pending.due = last + self._cooldown
                continue
            root_id, rel_dir = key
            # One pass over the disk for both open questions; None for a walk.
            look = await self._look_at(root_id, rel_dir, pending)
            # Asked first: a vanished folder makes every file read as still changing.
            if look is not None and not look.folder_is_there:
                self._folder_went(key, now)
                continue
            # Native notifications can go quiet while a file is still being written, so the file is
            # asked.
            if look is not None and look.sizes != pending.sizes:
                pending.sizes = look.sizes
                # A short confirmation, not another full settle-wait; never longer than it.
                pending.due = now + min(_SETTLE_RECHECK_SECONDS, self._quiet)
                continue
            del self._pending[key]
            self._last_scan[key] = now
            await self._scan(root_id, rel_dir, pending.paths)

    async def _look_at(self, root_id: str, rel_dir: str, pending: _Pending) -> _Look | None:
        """Ask the disk about a burst, off the loop: its named files, and whether its folder is
        there."""
        base = self._bases.get(root_id)
        if base is None:
            return None
        return await asyncio.to_thread(_measure, base, rel_dir, sorted(pending.paths or ()))

    def _folder_went(self, key: tuple[str, str], now: float) -> None:
        """The folder these files were in is gone, so the folder ABOVE is re-armed for a walk.
        Only the parent's listing can tell removed from renamed."""
        root_id, rel_dir = key
        del self._pending[key]
        if rel_dir == "":
            # The root's own directory: unmounted cannot be told from deleted, so nothing is marked
            # missing.
            log.info("library.watch_root_missing", root_id=root_id)
            return
        above = str(PurePosixPath(rel_dir).parent)
        parent = (root_id, "" if above == "." else above)
        waiting = self._pending.get(parent)
        if waiting is None:
            # The settle-wait was already served.
            self._pending[parent] = _Pending(due=now)
        else:
            # Keep its deadline, but make it a walk.
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
        # Deduped on the whole payload, and shaped by `scan_shape` so a walk of the top folder
        # matches a press.
        payload: dict[str, object] = scan_shape(root_id, folder)
        if paths:
            payload["paths"] = sorted(paths)
        try:
            await self._queue.enqueue(SCAN, payload, dedupe=True)
        # Caught here so the other folders ready this tick still go.
        except JobSwitchedOff:
            return
        log.info(
            "library.watch_triggered_scan",
            root_id=root_id,
            folder_id=folder.id,
            named=len(paths) if paths else None,
        )

    def _stop_observers(self) -> None:
        """Let go of every observer, never waiting on one for ever: `Observer.stop()` can block
        without bound."""
        watching = list(self._observers)
        handlers = list(self._handlers)
        self._observers.clear()
        self._handlers.clear()
        self._watches.clear()
        self._bases.clear()
        _let_go(watching, handlers)

    async def stop(self) -> None:
        # The attach first: it may still be handing an observer to the sweep.
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
    """Let go of these observers, and never wait on one for ever. Blocking; call it on a thread."""
    if not watching:
        return

    # First: nothing still running may reach the event loop again.
    for handler in handlers:
        handler.detach()

    def _ask() -> None:
        for observer in watching:
            # A stop that raises must not skip the ones after it.
            with contextlib.suppress(Exception):
                observer.stop()
        for observer in watching:
            observer.join(timeout=_STOP_SECONDS)

    asking = threading.Thread(target=_ask, name="library-watch-stop", daemon=True)
    asking.start()
    asking.join(_STOP_SECONDS * 2)
    if asking.is_alive():
        log.warning("library.watch_stop_abandoned", observers=len(watching))


WATCHER: Part[LibraryWatcher] = Part("watcher")
