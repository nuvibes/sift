# SPDX-License-Identifier: AGPL-3.0-or-later
"""Windows' own change notifications, ended the way Windows documents.
The read is overlapped and the handle is closed by the emitter's own thread once the cancelled
read
is collected, so no I/O is outstanding at close. Overflow and an ended watch are both reported."""

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

#: watchdog's set less last-access, so a pass that only reads files does not look like change.
WATCHED_CHANGES = WATCHDOG_FILE_NOTIFY_FLAGS & ~FILE_NOTIFY_CHANGE_LAST_ACCESS

_WAIT_TIMEOUT = 0x00000102

#: A share's overflow; a local disk completes with zero bytes instead.
ERROR_NOTIFY_ENUM_DIR = 1022


class _Offsets(ctypes.Structure):
    _fields_ = (("Offset", DWORD), ("OffsetHigh", DWORD))


class _Where(ctypes.Union):
    _fields_ = (("at", _Offsets), ("Pointer", LPVOID))


class _OVERLAPPED(ctypes.Structure):
    """The real `OVERLAPPED`: watchdog's puts `hEvent` at offset 32, where the kernel reads 24,
    so an overlapped read would never signal."""

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

#: With `bWait` it returns only once the cancelled read has finished with the buffer.
_GetOverlappedResult = _kernel32.GetOverlappedResult
_GetOverlappedResult.restype = BOOL
_GetOverlappedResult.argtypes = (HANDLE, _LP_OVERLAPPED, ctypes.POINTER(DWORD), BOOL)

_WaitForSingleObject = _kernel32.WaitForSingleObject
_WaitForSingleObject.restype = DWORD
_WaitForSingleObject.argtypes = (HANDLE, DWORD)

_INVALID_HANDLE = HANDLE(-1).value


class SafeWindowsApiEmitter(WindowsApiEmitter):
    """`WindowsApiEmitter`, with the read issued overlapped and the handle closed by its own thread."""

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
        #: Written once by the stopping thread, read by the emitter.
        self._letting_go = False

    def on_thread_start(self) -> None:
        # FILE_FLAG_OVERLAPPED is the difference.
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
            # Raised so `_observe` falls back to polling.
            raise OSError(ctypes.get_last_error(), "the folder could not be opened for watching")
        self._whandle = handle
        # Manual reset, so a completion is still seen if the thread was elsewhere.
        self._ready = _CreateEventW(None, True, False, None)
        self._overlapped.hEvent = self._ready

    def on_thread_stop(self) -> None:
        """Ask, and do not close. Runs on whichever thread called `stop()`."""
        self._letting_go = True
        if self._whandle:
            with _quietly():
                _CancelIoEx(self._whandle, ctypes.byref(self._overlapped))

    def run(self) -> None:
        """watchdog's loop, with the close run on the emitter's own thread after it."""
        try:
            super().run()
        finally:
            self._let_go()

    def _let_go(self) -> None:
        handle, self._whandle = self._whandle, None
        ready, self._ready = self._ready, None
        if handle:
            if self._reading:
                # Cancelled again here: a stop can land before the read went out, and the wait below
                # would never end.
                with _quietly():
                    _CancelIoEx(handle, ctypes.byref(self._overlapped))
                # Waits until the cancelled read is done; its expected failure must not skip the
                # close.
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

    def _read_events(self) -> list[WinAPINativeEvent]:
        """Wait up to the emitter's timeout for the outstanding read; nothing when it runs out."""
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
                # A stop cancelled the read.
                return []
            if error == ERROR_NOTIFY_ENUM_DIR:
                log.info("library.native_watch_overflowed", path=str(self.watch.path))
                if self._on_overflow is not None:
                    self._on_overflow()
                return []
            # The watch went deaf; re-issuing on this handle would spin.
            log.warning("library.native_watch_read_failed", error=error)
            self._end(error)
            return []
        if moved.value == 0:
            # Zero bytes means the buffer overflowed, not that nothing happened.
            log.info("library.native_watch_overflowed", path=str(self.watch.path))
            if self._on_overflow is not None:
                self._on_overflow()
            return []
        return [
            WinAPINativeEvent(action, src_path)
            for action, src_path in _parse_event_buffer(self._buffer.raw, moved.value)
        ]

    def _begin(self) -> bool:
        """Issue the next overlapped read. False when the directory has gone."""
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
            # Not fatal: the watcher re-attaches once the folder answers.
            self._end(ctypes.get_last_error())
            return False
        self._reading = True
        return True

    def _end(self, error: int) -> None:
        """This watch cannot go on: stop the loop and tell the listener once, never for a requested
        stop."""
        if self._letting_go:
            return
        log.info("library.native_watch_ended", path=str(self.watch.path), error=error)
        self.stop()
        if self._on_ended is not None:
            self._on_ended()


class SafeWindowsApiObserver(BaseObserver):
    """`WindowsApiObserver` with the emitter above, reporting an overflow and an ended watch."""

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_OBSERVER_TIMEOUT,
        on_overflow: Callable[[], None] | None = None,
        on_ended: Callable[[], None] | None = None,
    ) -> None:
        # `BaseObserver` builds each emitter itself, so a partial is the only way to pass arguments.
        made = partial(SafeWindowsApiEmitter, on_overflow=on_overflow, on_ended=on_ended)
        super().__init__(cast("type[EventEmitter]", made), timeout=timeout)


class _quietly:
    """Swallow a Win32 teardown failure so the CloseHandle after it still runs."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, *_: object) -> bool:
        return True
