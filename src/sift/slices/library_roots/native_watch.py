# SPDX-License-Identifier: AGPL-3.0-or-later
"""Windows' own change notifications, ended the way Windows documents.

## What this is for

`ReadDirectoryChangesW` is how Windows tells a program that a directory changed. It is immediate
(on an SMB share a file created is reported within milliseconds), and it costs nothing while
nothing is happening, because the operating system is doing the watching. The alternative is
walking the tree over and over, which on a library of hundreds of thousands of files is a
continuous read of a disk nobody touched.

## Why watchdog's own version could not be used

watchdog issues that call SYNCHRONOUSLY (`lpOverlapped` is `None`), so the emitter thread parks
inside it until something changes. The only way to end a synchronous read is to cancel it, and
watchdog's `close_directory_handle` calls `CancelIoEx` and then `CloseHandle` immediately, from a
DIFFERENT thread, while the read may still be writing into the buffer it was handed. `BaseThread.stop`
calls that before the emitter is joined, so the close is guaranteed to race the read.

Closing a handle with I/O outstanding on it is undefined, and both of the things it is undefined
into happen in practice: the read never returns, so stopping a watch never finishes; and the
completion lands in memory that has been given back, which takes the process down with an access
violation.

## What this does instead

The read is **overlapped**, so it returns immediately and completes into an event. The emitter waits
on that event with a timeout, which is what lets it notice it has been asked to stop between waits
rather than only when a file changes. Stopping cancels the outstanding read and does NOT close
anything; the handle is closed by the emitter's OWN thread, after its loop has finished and after
the cancelled read has been collected. There is never an outstanding operation at close time, so the
undefined behaviour is removed rather than sequenced around.

Everything else (turning a raw action into a `FileSystemEvent`, the renames, the sub-events) is
watchdog's and is inherited untouched. What is written here is the four calls that end a read safely.

## Overflow is a real state and must be reported

The operating system buffers changes into the buffer it was given. Fill it faster than it is drained
(a copy of ten thousand files, which is an ordinary thing to do to a library) and the read
completes with **zero bytes**, meaning "changes were lost, enumerate the directory yourself". Treated
as "nothing happened", every file in that burst would be silently missed. `on_overflow` is how the
watcher hears about it, and what it does is ask for the catch-up pass that already exists.

## A watch that ends is reported too, and is not restarted here

A share that drops, a folder that goes, a handle the system no longer honours: the read fails, or
the next one will not go out. Re-issuing it on the same handle answers the same no at once, so the
loop that asks again would spin a core and notice nothing for as long as Sift ran. So the emitter
stops its own loop, lets go of its handle the ordinary way, and calls `on_ended`: once, and never
for a stop somebody asked for. Coming back is the watcher's: a new handle when the folder answers
again, and the catch-up pass for whatever arrived while nobody was listening.
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from ctypes.wintypes import BOOL, DWORD, HANDLE, LPCWSTR, LPVOID
from functools import partial
from typing import TYPE_CHECKING, Any, cast

from watchdog.observers.api import DEFAULT_OBSERVER_TIMEOUT, BaseObserver, EventEmitter
from watchdog.observers.read_directory_changes import WindowsApiEmitter
from watchdog.observers.winapi import (
    BUFFER_SIZE,
    ERROR_OPERATION_ABORTED,
    FILE_FLAG_BACKUP_SEMANTICS,
    FILE_FLAG_OVERLAPPED,
    FILE_LIST_DIRECTORY,
    FILE_NOTIFY_CHANGE_LAST_ACCESS,
    OPEN_EXISTING,
    WATCHDOG_FILE_NOTIFY_FLAGS,
    WATCHDOG_FILE_SHARE_FLAGS,
    WinAPINativeEvent,
    _parse_event_buffer,
)

from sift.kernel.log import get_logger

if TYPE_CHECKING:
    from watchdog.observers.api import EventQueue, ObservedWatch

log = get_logger(__name__)

#: What a watch asks Windows to report: watchdog's own set, less the last-access time. Reading a
#: file moves that time on a volume that keeps it, so a pass that only reads the library (a
#: fingerprint, a face scan, a play) would be reported as every file it opened changing, and each
#: report asks for a scan of the folder. A scan decides by name, size and modification time, and a
#: change to any of those is still reported.
WATCHED_CHANGES = WATCHDOG_FILE_NOTIFY_FLAGS & ~FILE_NOTIFY_CHANGE_LAST_ACCESS

#: `WaitForSingleObject` said the wait ran out rather than the event being signalled.
_WAIT_TIMEOUT = 0x00000102

#: A read that completed with "too many changes, list the directory yourself". A share reports its
#: overflow this way where a local disk completes with zero bytes, and it means the same thing.
ERROR_NOTIFY_ENUM_DIR = 1022


class _Offsets(ctypes.Structure):
    _fields_ = (("Offset", DWORD), ("OffsetHigh", DWORD))


class _Where(ctypes.Union):
    _fields_ = (("at", _Offsets), ("Pointer", LPVOID))


class _OVERLAPPED(ctypes.Structure):
    """The real `OVERLAPPED`, because watchdog's is the wrong shape.

    !! THIS IS NOT A STYLE PREFERENCE. watchdog declares `Offset`,
    `OffsetHigh` AND `Pointer` as three separate fields. In the Win32 structure the first two are a
    UNION with the third, so watchdog's is **40 bytes with `hEvent` at offset 32**, and the real
    one is **32 bytes with `hEvent` at offset 24**.

    The kernel reads `hEvent` from offset 24 whatever Python thinks. Handed watchdog's struct, it
    reads the `Pointer` field (zero), concludes there is no event to signal, and completes the
    read against the file handle instead. Everything looks right: the handle opens, the read is
    accepted, no error is raised anywhere (`last error: 0`), and not one notification ever arrives.

    watchdog is not wrong to have shipped it: it never issues an overlapped read, so it never
    fills this field in and the size never mattered. It matters here, so the struct is declared
    correctly here, and so are the four calls that take a pointer to one.
    """

    _fields_ = (
        ("Internal", LPVOID),
        ("InternalHigh", LPVOID),
        ("where", _Where),
        ("hEvent", HANDLE),
    )


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_LP_OVERLAPPED = ctypes.POINTER(_OVERLAPPED)

_CreateFileW = _kernel32.CreateFileW
_CreateFileW.restype = HANDLE
_CreateFileW.argtypes = (LPCWSTR, DWORD, DWORD, LPVOID, DWORD, DWORD, HANDLE)

_CreateEventW = _kernel32.CreateEventW
_CreateEventW.restype = HANDLE
_CreateEventW.argtypes = (LPVOID, BOOL, BOOL, LPCWSTR)

_ResetEvent = _kernel32.ResetEvent
_ResetEvent.restype = BOOL
_ResetEvent.argtypes = (HANDLE,)

_CloseHandle = _kernel32.CloseHandle
_CloseHandle.restype = BOOL
_CloseHandle.argtypes = (HANDLE,)

_CancelIoEx = _kernel32.CancelIoEx
_CancelIoEx.restype = BOOL
_CancelIoEx.argtypes = (HANDLE, _LP_OVERLAPPED)

_ReadDirectoryChangesW = _kernel32.ReadDirectoryChangesW
_ReadDirectoryChangesW.restype = BOOL
_ReadDirectoryChangesW.argtypes = (
    HANDLE,
    LPVOID,
    DWORD,
    BOOL,
    DWORD,
    ctypes.POINTER(DWORD),
    _LP_OVERLAPPED,
    LPVOID,
)

#: The call that makes the close safe. Asked with `bWait`, it does not return until the cancelled
#: read has finished with the buffer, so there is no outstanding operation when the handle goes.
_GetOverlappedResult = _kernel32.GetOverlappedResult
_GetOverlappedResult.restype = BOOL
_GetOverlappedResult.argtypes = (HANDLE, _LP_OVERLAPPED, ctypes.POINTER(DWORD), BOOL)

_WaitForSingleObject = _kernel32.WaitForSingleObject
_WaitForSingleObject.restype = DWORD
_WaitForSingleObject.argtypes = (HANDLE, DWORD)

#: A handle that is not a handle. `CreateFileW` reports failure this way rather than by raising.
_INVALID_HANDLE = HANDLE(-1).value


class SafeWindowsApiEmitter(WindowsApiEmitter):
    """`WindowsApiEmitter`, with the read issued overlapped and the handle closed by its own thread.

    Only `_read_events` and the lifecycle are replaced. `queue_events` is watchdog's, so the
    translation from a raw action into created / modified / moved / deleted (including the two-part
    rename and the sub-events for a directory) stays exactly where it is maintained.
    """

    def __init__(
        self,
        event_queue: EventQueue,
        watch: ObservedWatch,
        *,
        timeout: float = DEFAULT_OBSERVER_TIMEOUT,
        event_filter: list[type[Any]] | None = None,
        on_overflow: Callable[[], None] | None = None,
        on_ended: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(event_queue, watch, timeout=timeout, event_filter=event_filter)
        self._on_overflow = on_overflow
        self._on_ended = on_ended
        self._buffer = ctypes.create_string_buffer(BUFFER_SIZE)
        self._overlapped = _OVERLAPPED()
        self._ready: int | None = None
        self._reading = False
        #: Set from the thread that asks for the stop, read by the emitter's own thread. A flag
        #: written once and never back is the one thing two threads may share without a lock.
        self._letting_go = False

    # --- the lifecycle, which is the whole point of this class ------------------------------

    def on_thread_start(self) -> None:
        # FILE_FLAG_OVERLAPPED is the difference. Without it the read blocks and can only be ended
        # by a cancellation racing a close, which is what this class exists to stop.
        handle = _CreateFileW(
            self.watch.path,
            FILE_LIST_DIRECTORY,
            WATCHDOG_FILE_SHARE_FLAGS,
            None,
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OVERLAPPED,
            None,
        )
        if not handle or handle == _INVALID_HANDLE:
            # Raised rather than carried, so `_observe` falls back to polling. A directory that will
            # not open is the one honest reason to walk it instead.
            raise OSError(ctypes.get_last_error(), "the folder could not be opened for watching")
        self._whandle = handle
        # Manual reset, so a completion that lands while the thread is elsewhere is still waiting
        # to be seen rather than having been consumed by nobody.
        self._ready = _CreateEventW(None, True, False, None)
        self._overlapped.hEvent = self._ready

    def on_thread_stop(self) -> None:
        """Ask, and do not close. Runs on whichever thread called `stop()`.

        `CancelIoEx` only requests the cancellation: it returns before the read has necessarily
        finished with the buffer. So the close cannot happen here, and does not: it happens in
        `run`, on the emitter's own thread, once the loop is over and the read has been collected.
        """
        self._letting_go = True
        if self._whandle:
            with _quietly():
                _CancelIoEx(self._whandle, ctypes.byref(self._overlapped))

    def run(self) -> None:
        """watchdog's loop, with the letting-go bolted to the end of it.

        `BaseThread.stop` calls `on_thread_stop` from the CALLER's thread; there is no hook that
        runs on the emitter's own thread after the loop. This is that hook, and it is why the close
        is safe: by the time it runs, this thread is the only one left touching the handle.
        """
        try:
            super().run()
        finally:
            self._let_go()

    def _let_go(self) -> None:
        handle, self._whandle = self._whandle, None
        ready, self._ready = self._ready, None
        if handle:
            if self._reading:
                # CANCELLED AGAIN, HERE, AND THIS IS WHAT MAKES THE WAIT BELOW BOUNDED.
                #
                # `on_thread_stop` cancels from the CALLER's thread, and it can land in the window
                # between this emitter reading `_letting_go` as false and the read actually being
                # issued. `CancelIoEx` with nothing outstanding cancels nothing (it answers
                # ERROR_NOT_FOUND, which is swallowed). The read then goes out a moment later, and
                # the collect below waits for a change that may never come. **That is a stop that
                # never returns, and it is not theoretical**: a test run hangs on it for as long as
                # nothing interrupts it.
                #
                # Safe here in a way it is not there: this is the emitter's own thread, the loop is
                # over, and nothing else is left touching the handle, which is the same argument
                # that lets the close happen here rather than in `on_thread_stop`.
                with _quietly():
                    _CancelIoEx(handle, ctypes.byref(self._overlapped))
                # THE ONE CALL THAT MAKES THE CLOSE SAFE. With `bWait` it does not return until the
                # cancelled read has finished with the buffer, so there is no outstanding operation
                # when the handle goes. Its failure is expected (a cancelled read reports
                # ERROR_OPERATION_ABORTED) and is not a reason to skip the close.
                moved = DWORD()
                with _quietly():
                    _GetOverlappedResult(
                        handle, ctypes.byref(self._overlapped), ctypes.byref(moved), True
                    )
                self._reading = False
            with _quietly():
                _CloseHandle(handle)
        if ready:
            with _quietly():
                _CloseHandle(ready)

    # --- one wait for changes ---------------------------------------------------------------

    def _read_events(self) -> list[WinAPINativeEvent]:
        """Wait up to the emitter's timeout for the outstanding read to complete.

        Returns nothing when the wait runs out, which is the ordinary case: the loop calls again,
        and between the two it checks whether it has been asked to stop. That check is why the read
        has to be overlapped: a synchronous one gives the thread no moment to notice.
        """
        if not self._whandle or self._letting_go:
            return []
        if not self._reading and not self._begin():
            return []

        waited = _WaitForSingleObject(self._ready, int(max(self.timeout, 0.0) * 1000))
        if waited == _WAIT_TIMEOUT:
            return []

        moved = DWORD()
        ok = _GetOverlappedResult(
            self._whandle, ctypes.byref(self._overlapped), ctypes.byref(moved), False
        )
        self._reading = False
        if not ok:
            error = ctypes.get_last_error()
            if error == ERROR_OPERATION_ABORTED:
                # The letting-go working: a stop cancelled the read. Nothing to say.
                return []
            if error == ERROR_NOTIFY_ENUM_DIR:
                # A share's overflow. The same answer as the zero-byte one below.
                log.info("library.native_watch_overflowed", path=str(self.watch.path))
                if self._on_overflow is not None:
                    self._on_overflow()
                return []
            # Anything else is the watch going deaf (a share dropping is one way here, a folder
            # removed another), and re-issuing the read on this handle is the spin `_end` stops.
            log.warning("library.native_watch_read_failed", error=error)
            self._end(error)
            return []
        if moved.value == 0:
            # The buffer overflowed: the operating system had more changes than it could hold and
            # threw them away. Reported rather than read as quiet, because "nothing happened" and
            # "everything happened at once" are the same zero.
            log.info("library.native_watch_overflowed", path=str(self.watch.path))
            if self._on_overflow is not None:
                self._on_overflow()
            return []
        return [
            WinAPINativeEvent(action, src_path)
            for action, src_path in _parse_event_buffer(self._buffer.raw, moved.value)
        ]

    def _begin(self) -> bool:
        """Issue the next overlapped read. False when the directory has gone.

        The event is reset by hand first. Windows does that itself when it accepts an overlapped
        operation, and saying so here costs one call and removes a dependency on a documented
        side effect, which is the sort of thing that is right until a version where it is not.
        """
        _ResetEvent(self._ready)
        ok = _ReadDirectoryChangesW(
            self._whandle,
            ctypes.byref(self._buffer),
            len(self._buffer),
            self.watch.is_recursive,
            WATCHED_CHANGES,
            ctypes.byref(DWORD()),
            ctypes.byref(self._overlapped),
            None,
        )
        if not ok:
            # The folder was removed, the share went away, or the handle is no longer good. Not
            # fatal and not this class's to decide about: the emitter stops and says so, and the
            # watcher attaches again once the folder answers and catches up what it missed.
            self._end(ctypes.get_last_error())
            return False
        self._reading = True
        return True

    def _end(self, error: int) -> None:
        """This watch cannot go on. Stop the loop, and tell whoever is listening, once.

        Never for a stop somebody asked for: that sets `_letting_go` first, and a watch being let
        go that fails on its way out is the letting-go, not the folder going. The loop ends on the
        stop flag and `run` lets go of the handle exactly as it would for an ordinary stop; nothing
        is closed here.
        """
        if self._letting_go:
            return
        log.info("library.native_watch_ended", path=str(self.watch.path), error=error)
        self.stop()
        if self._on_ended is not None:
            self._on_ended()


class SafeWindowsApiObserver(BaseObserver):
    """`WindowsApiObserver` with the emitter above, and a way to hear about an overflow and about
    a watch that has ended."""

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_OBSERVER_TIMEOUT,
        on_overflow: Callable[[], None] | None = None,
        on_ended: Callable[[], None] | None = None,
    ) -> None:
        # `partial` rather than a class, because `BaseObserver` builds each emitter itself and
        # there is no other way to hand one an argument of our own. Typed as a class there; a
        # partial satisfies the call it is actually put to, which is all the base ever does with it.
        made = partial(SafeWindowsApiEmitter, on_overflow=on_overflow, on_ended=on_ended)
        super().__init__(cast("type[EventEmitter]", made), timeout=timeout)


class _quietly:
    """Swallow whatever a Win32 call raises while letting go.

    Every call inside one of these is a teardown step, and each one is allowed to fail for an
    ordinary reason: a handle already invalid, a share already gone. What must not happen is a
    raise that skips the CloseHandle after it.
    """

    def __enter__(self) -> None:
        return None

    def __exit__(self, *_: object) -> bool:
        return True
