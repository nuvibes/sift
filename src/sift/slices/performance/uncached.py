# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading places in a file straight from the disk, past the system's cache.

A benchmark of a local disk that reads through the cache measures memory. Windows opens the file
unbuffered; elsewhere each place is dropped from the cache before it is read.
"""

from __future__ import annotations

import mmap
import os
import sys
from collections.abc import Sequence
from functools import cache
from pathlib import Path

#: Places and sizes are on this boundary, which an unbuffered read needs.
ALIGN = 4096

#: Whether this system can read past its cache at all.
AVAILABLE = sys.platform == "win32" or hasattr(os, "posix_fadvise")


def read(path: Path, places: Sequence[int], size: int) -> int:
    """Read `size` bytes at each of `places`, all aligned. How many bytes came back."""
    buffer = mmap.mmap(-1, size)  # page aligned, as an unbuffered read needs
    try:
        return _read(path, places, buffer)
    finally:
        buffer.close()


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    @cache
    def _kernel32() -> ctypes.WinDLL:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CreateFileW.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        kernel32.SetFilePointerEx.argtypes = (
            wintypes.HANDLE,
            ctypes.c_longlong,
            wintypes.LPVOID,
            wintypes.DWORD,
        )
        kernel32.ReadFile.argtypes = (
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        )
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        return kernel32

    #: Read access, every share mode, an existing file, and no buffering by the system.
    _OPEN = (0x80000000, 7, None, 3, 0x20000000, None)

    def _read(path: Path, places: Sequence[int], buffer: mmap.mmap) -> int:
        kernel32 = _kernel32()
        handle = kernel32.CreateFileW(str(path), *_OPEN)
        if handle is None or handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        target = (ctypes.c_char * len(buffer)).from_buffer(buffer)
        try:
            read = 0
            came = wintypes.DWORD(0)
            for place in places:
                if not kernel32.SetFilePointerEx(handle, place, None, 0) or not kernel32.ReadFile(
                    handle, ctypes.addressof(target), len(buffer), ctypes.byref(came), None
                ):
                    raise ctypes.WinError(ctypes.get_last_error())
                read += came.value
            return read
        finally:
            del target
            kernel32.CloseHandle(handle)

else:  # pragma: no cover (the suite runs on Windows)

    def _read(path: Path, places: Sequence[int], buffer: mmap.mmap) -> int:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            read = 0
            for place in places:
                os.posix_fadvise(descriptor, place, len(buffer), os.POSIX_FADV_DONTNEED)
                read += os.preadv(descriptor, [buffer], place)
            return read
        finally:
            os.close(descriptor)
