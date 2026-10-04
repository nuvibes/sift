# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sending a folder (or a file) to the Recycle Bin, where it can be put back from.

The one way this feature removes a library, because a library is somebody's work and a delete that
cannot be taken back is not a thing a settings page should do on a typed name alone. So it is
Windows' own move to the bin (`SHFileOperationW` with undo allowed), after the checks that say the
move really will be one:

- The folder is on a fixed drive. A network share, a memory stick and a mapped drive have no bin
  of their own, and Windows deletes from them permanently even when asked to keep a way back.
- That drive has a Recycle Bin (`SHQueryRecycleBinW` answers for it).
- And a folder too large for the bin, which Windows would also delete permanently, is caught by asking
  Windows to warn first (`FOF_WANTNUKEWARNING`): that one case is decided at the machine, by the
  person there, because nothing here can read the bin's size limit ahead of the move.

Where there is no Recycle Bin at all (Sift running in a container, or on Linux), nothing is deleted
and the refusal says where the folder is, so it can be removed by hand.

A backup somebody deletes from the Backup pane takes the same move where its folder's drive has a
bin (`has_recycle_bin`); where it has none, the pane says before the press that the delete is for
good.
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path


class NoRecycleBin(Exception):
    """The folder cannot be moved to a Recycle Bin, so it has been left where it is."""


#: `SHFileOperationW`'s own numbers. See the Windows documentation for SHFILEOPSTRUCTW.
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
    """Whether a file or folder in `place` would go to a Recycle Bin if Sift deleted it. Blocking.

    The two checks `to_recycle_bin` makes before its move, asked ahead of it so a screen can say
    which of the two a Delete will be: a fixed drive, and a bin on that drive. False off Windows.
    """
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
    """Move `folder` and everything in it to the Recycle Bin. Blocking. Raises `NoRecycleBin`.

    Never called by a test: the move is into the real bin of whoever runs the suite. The checks in
    front of it are what the tests reach, through the caller's own refusals.
    """
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
