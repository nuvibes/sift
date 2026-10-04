# SPDX-License-Identifier: AGPL-3.0-or-later
"""Proving a path is inside a directory: the one way, fail-closed.

An untrusted path can carry `..`, an absolute path, or a symlink out of its directory, which no
string check sees; so both sides are resolved and the candidate proved to sit under the root, and
anything that will not resolve counts as an escape. One shared version, so every caller refuses the
same failures; each turns `PathEscape` into its own layer's answer. `resolve()` reads the disk:
call it off the event loop.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import Literal

from sift.kernel.threads import waits_on_storage

# A flag rather than a literal test, so mypy checks both platforms' branches on either host
# instead of calling the other platform's half unreachable.
_WINDOWS = sys.platform == "win32"


# Opening a file without waiting for it. `os.O_NONBLOCK` does not exist on Windows (reaching for it
# is a crash on the import path), and the hazard it guards, a named pipe in a library folder, has
# no path form there, so zero is correct. Both callers still test S_ISREG on the OPEN descriptor,
# which is what rejects anything but a plain file without a race.
O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)


class PathEscape(Exception):
    """A candidate path does not resolve to somewhere inside its root."""


def confine(root: Path, candidate: Path) -> Path:
    """Return `candidate` resolved, having proved it is `root` or sits inside it. Raise `PathEscape`
    otherwise.

    Both sides are resolved, so a root reached through a symlink is not an escape, and a candidate
    leaving by `..`, an absolute path or a symlink is. Fail-closed: what will not resolve is an
    escape. A symlink loop raises on some Python versions and comes back unchanged on others, so a
    symlink returned by `resolve()` also counts as giving up. Being unable to ask (a folder whose
    permissions vanished mid-pass) is NOT an escape; the containment check then decides.
    """
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
    """Whether the filesystem this path sits on was mounted read-only.

    The hard half: a read-only mount refuses below the process, whatever Sift or anything that took
    it over attempts, which is why read-only is the offered default. A path that cannot be asked
    reads as read-only: withholding a capability is the harmless way to be wrong. Blocks: call it
    off the event loop.
    """
    if _WINDOWS:
        return _windows_volume_is_readonly(path)
    try:
        # `statvfs` is absent on Windows, so the ignore is needed there and unused in CI.
        return bool(os.statvfs(path).f_flag & os.ST_RDONLY)  # type: ignore[attr-defined, unused-ignore]
    except OSError:
        return True


def _windows_volume_is_readonly(path: Path) -> bool:
    """The same question on Windows, which has no `statvfs`.

    Without it every folder in the picker would raise and the browse endpoint answer 500. The
    volume's own `FILE_READ_ONLY_VOLUME` flag (via `GetVolumePathNameW` and
    `GetVolumeInformationW`) is the analogue of `ST_RDONLY`, asked without writing anything, and
    fails closed alike.
    """
    import ctypes
    from ctypes import wintypes

    FILE_READ_ONLY_VOLUME = 0x0008_0000
    try:
        # Asked first: `GetVolumePathNameW` parses the string without a lookup, so a deleted folder
        # would report a writable volume where the POSIX branch fails closed.
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
    """Whether Sift could actually create a file in this directory.

    The mount must allow writing and the account Sift runs as must have permission (files owned by
    another user are the ordinary failure), proved without writing anything into a library. Blocks:
    call it off the event loop. On Windows `os.access` ignores the access-control entries, so the
    second half is best effort there: a wrong yes fails at the write with the ordinary error, and
    writable roots are an explicit choice that defaults off.
    """
    return not mount_is_readonly(path) and os.access(path, os.W_OK)


def same_filesystem(one: Path, other: Path) -> bool:
    """Whether two paths sit on the same filesystem, so a move between them is a rename.

    Within one filesystem a move is an instant, atomic rename; across two it is a copy and delete
    with other failure modes. `st_dev` is what `os.rename` itself consults, so this asks the same
    question the operation asks. A path that cannot be asked reads as different, refusing a move
    rather than promising a rename that fails with EXDEV midway. Blocks: call it off the event loop.
    """
    try:
        return os.stat(one).st_dev == os.stat(other).st_dev
    except OSError:
        return False


__all__ = ["PathEscape", "confine", "is_writable", "mount_is_readonly", "same_filesystem"]


#: Windows codes `os.stat` raises as FileNotFoundError when the SHARE did not answer, not the file
#: (53 network path, 64 network name, 1231 network unreachable); only 2 is a file not there.
_NOTHING_ANSWERED = frozenset({53, 64, 1231})

#: Windows' "path not found": a drive letter with nothing mounted, or a file whose FOLDER is gone on
#: a present drive. Read as "nothing answered" without asking the root, files under a folder deleted
#: in Explorer would stay on the walls for good.
_PATH_NOT_FOUND = 3


def is_absence(error: OSError, *, under: Path | None = None) -> bool:
    """Whether an error from looking at a path means the file is not there.

    Anything else (refused, a mount gone away, a silent share) is about ACCESS, not existence; on
    Windows the error's code tells a definite no from a share that is off. `under` settles the
    ambiguous "path not found": a root that answers means the folder is gone. Asking reads the disk,
    so pass `under` only off the event loop.
    """
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


#: Where a library folder stands, in the three states a screen can prove: it answers ("here");
#: nothing answered at all, a share that is off or a drive with nothing mounted ("silent"); or the
#: drive answered and the folder is not on it ("missing").
Presence = Literal["here", "silent", "missing"]


@waits_on_storage
def presence(path: Path) -> Presence:
    """Whether a folder is there, and when it is not, whether the drive it lives on answered.

    Built on `is_absence`, so "missing" needs a definite no from a drive that is there; any other
    failure is "silent", since calling a network fault a gone folder is the wrong sentence. Off
    Windows no code tells a lost mount from a lost folder, so the parent must answer first. A path
    that is there but not a folder is "missing". Can wait on a dead share for the mount's timeout:
    call it off the event loop under the caller's own timeout.
    """
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
