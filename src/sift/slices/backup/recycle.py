# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sending a folder or file to the Recycle Bin, only where the move really will be one."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path


class NoRecycleBin(Exception):
    """The folder cannot be moved to a Recycle Bin, so it has been left where it is."""


#: `SHFileOperationW`'s own numbers (see SHFILEOPSTRUCTW).
_FO_DELETE = 0x0003
_FOF_SILENT = 0x0004
_FOF_NOCONFIRMATION = 0x0010
_FOF_ALLOWUNDO = 0x0040
_FOF_NOERRORUI = 0x0400
_FOF_WANTNUKEWARNING = 0x4000
_DRIVE_FIXED = 3


def _structures() -> tuple[type[ctypes.Structure], type[ctypes.Structure]]:
    """The two structures the calls take, built on Windows only (`wintypes` is Windows' own)."""
    from ctypes import wintypes

    class ShFileOp(ctypes.Structure):
        _fields_ = (
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_uint16),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        )

    class BinInfo(ctypes.Structure):
        _fields_ = (
            ("cbSize", wintypes.DWORD),
            ("i64Size", ctypes.c_longlong),
            ("i64NumItems", ctypes.c_longlong),
        )

    return ShFileOp, BinInfo


def has_recycle_bin(place: Path) -> bool:  # pragma: no cover (asks this machine's Windows)
    """Whether `place` would go to a Recycle Bin if deleted: a fixed drive with a bin. Blocking."""
    if sys.platform != "win32":
        return False
    drive = place.resolve().anchor
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if kernel32.GetDriveTypeW(ctypes.c_wchar_p(drive)) != _DRIVE_FIXED:
        return False
    _operation, bininfo = _structures()
    info = bininfo()
    info.cbSize = ctypes.sizeof(bininfo)
    return bool(shell32.SHQueryRecycleBinW(ctypes.c_wchar_p(drive), ctypes.byref(info)) == 0)


def to_recycle_bin(
    folder: Path,
) -> None:  # pragma: no cover (moves a real folder into this machine's bin)
    """Move `folder` to the Recycle Bin. Blocking. Raises `NoRecycleBin`. Never run by a test."""
    if sys.platform != "win32":
        raise NoRecycleBin(
            f"This computer has no Recycle Bin that Sift can use, so nothing was deleted. "
            f"The library's folder is {folder}; delete it yourself if you're sure."
        )
    target = folder.resolve()
    drive = target.anchor
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if kernel32.GetDriveTypeW(ctypes.c_wchar_p(drive)) != _DRIVE_FIXED:
        raise NoRecycleBin(
            "That library is on a drive without a Recycle Bin (a network share or a removable "
            "drive), where Windows would delete it permanently. Nothing was deleted."
        )
    shfileop, bininfo = _structures()
    info = bininfo()
    info.cbSize = ctypes.sizeof(bininfo)
    if shell32.SHQueryRecycleBinW(ctypes.c_wchar_p(drive), ctypes.byref(info)) != 0:
        raise NoRecycleBin(
            "Windows has no Recycle Bin on that library's drive, so nothing was deleted."
        )
    operation = shfileop()
    operation.wFunc = _FO_DELETE
    # A list of paths, each ended by a NUL, and the list ended by one more.
    operation.pFrom = str(target) + "\0\0"
    operation.fFlags = (
        _FOF_ALLOWUNDO | _FOF_NOCONFIRMATION | _FOF_SILENT | _FOF_NOERRORUI | _FOF_WANTNUKEWARNING
    )
    failed = shell32.SHFileOperationW(ctypes.byref(operation))
    if failed or operation.fAnyOperationsAborted:
        raise NoRecycleBin(
            "Windows didn't move the library to the Recycle Bin. Nothing was deleted."
        )
    if target.exists():
        raise NoRecycleBin(
            "Windows moved only part of the library to the Recycle Bin. Put it back from there, "
            "then try again."
        )
