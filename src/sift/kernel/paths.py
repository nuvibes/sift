# SPDX-License-Identifier: AGPL-3.0-or-later
"""Proving a path is inside a directory, fail-closed: what will not resolve is an escape."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import Literal

from sift.kernel.threads import waits_on_storage

# A flag, so mypy checks both platforms' branches on either host.
_WINDOWS = sys.platform == "win32"


# Absent on Windows, where a named pipe has no path form; callers test S_ISREG on the open fd.
O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)


class PathEscape(Exception):
    """A candidate path does not resolve to somewhere inside its root."""


def confine(root: Path, candidate: Path) -> Path:
    """`candidate` resolved and proved inside `root`, else `PathEscape`; a symlink loop escapes."""
    try:
        resolved_root = root.resolve()
        resolved = candidate.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise PathEscape(f"{candidate} does not resolve") from exc
    try:
        gave_up = resolved.is_symlink()
    except OSError:
        gave_up = False
    if gave_up:
        raise PathEscape(f"{candidate} does not resolve")
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise PathEscape(f"{candidate} is not inside {root}")
    return resolved


def mount_is_readonly(path: Path) -> bool:
    """Whether this path's filesystem is mounted read-only; unanswerable reads as read-only."""
    if _WINDOWS:
        return _windows_volume_is_readonly(path)
    try:
        # `statvfs` is absent on Windows, so the ignore is needed there and unused in CI.
        return bool(os.statvfs(path).f_flag & os.ST_RDONLY)  # type: ignore[attr-defined, unused-ignore]
    except OSError:
        return True


def _windows_volume_is_readonly(path: Path) -> bool:
    """The same question on Windows, from the volume's own read-only flag; fails closed."""
    import ctypes
    from ctypes import wintypes

    FILE_READ_ONLY_VOLUME = 0x0008_0000
    try:
        # Asked first, so a deleted folder fails closed as on POSIX.
        os.stat(path)

        # `WinDLL` is absent off Windows, so the ignore carries both codes for both hosts.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined, unused-ignore]

        volume = ctypes.create_unicode_buffer(261)
        if not kernel32.GetVolumePathNameW(str(path), volume, len(volume)):
            return True

        flags = wintypes.DWORD()
        got = kernel32.GetVolumeInformationW(
            volume, None, 0, None, None, ctypes.byref(flags), None, 0
        )
        if not got:
            return True
        return bool(flags.value & FILE_READ_ONLY_VOLUME)
    except (OSError, AttributeError, ValueError):
        return True


def is_writable(path: Path) -> bool:
    """Whether Sift could create a file here, proved without writing; best effort on Windows."""
    return not mount_is_readonly(path) and os.access(path, os.W_OK)


def same_filesystem(one: Path, other: Path) -> bool:
    """Whether two paths share a filesystem, so a move is a rename; unanswerable reads as no."""
    try:
        return os.stat(one).st_dev == os.stat(other).st_dev
    except OSError:
        return False


__all__ = ["PathEscape", "confine", "is_writable", "mount_is_readonly", "same_filesystem"]


#: Windows codes for a share that did not answer; only 2 means the file is not there.
_NOTHING_ANSWERED = frozenset({53, 64, 1231})

#: A missing drive or a gone folder; the root is asked to tell which.
_PATH_NOT_FOUND = 3


def is_absence(error: OSError, *, under: Path | None = None) -> bool:
    """Whether an error means the file is not there, rather than a problem of access."""
    if not isinstance(error, FileNotFoundError):
        return False
    code = getattr(error, "winerror", None)
    if code is None:
        return True
    if code == _PATH_NOT_FOUND:
        if under is None:
            return False
        try:
            os.stat(under)
        except OSError:
            return False
        return True
    return code not in _NOTHING_ANSWERED


#: It answers; nothing answered ("silent"); or the drive answered without it ("missing").
Presence = Literal["here", "silent", "missing"]


@waits_on_storage
def presence(path: Path) -> Presence:
    """Whether a folder is here, missing, or silent behind a drive that did not answer."""
    try:
        found = os.stat(path)
    except OSError as error:
        if not is_absence(error, under=Path(path.anchor) if path.anchor else None):
            return "silent"
        if getattr(error, "winerror", None) is None:
            try:
                os.stat(path.parent)
            except OSError:
                return "silent"
        return "missing"
    return "here" if stat.S_ISDIR(found.st_mode) else "missing"
