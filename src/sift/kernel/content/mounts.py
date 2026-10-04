# SPDX-License-Identifier: AGPL-3.0-or-later
"""Whether a directory lives on this machine or on another one.

Sift treats the two differently, and has to: a local directory reports a change the moment a file
lands in it, and a network share does not report anything at all. A share is therefore looked at on
a timer instead, which costs something, so applying it to every folder would be wasteful, and
applying it to none would leave a share that never notices new files.

It is not a question anybody should be asked on the add-a-folder screen:
the machine already knows, and somebody who has just navigated to a directory is being asked to
classify their own filesystem, which is a thing they can get wrong and have no way to check.

What "another machine" means here is the filesystem the directory is mounted from. Linux names it,
and the names have been stable for as long as the protocols have existed.

**Windows has no mount table, and answering "local" for everything is not the safe default it
looks like.** `/proc/self/mounts` does not exist there, so without the branch below every root
(including a NAS reached over SMB) would read as local and be given a real filesystem watcher.
Windows does raise change notifications for a network drive, but not reliably and not for changes
another machine made, so what that produces is a share that silently stops noticing new files while
the screen says it is being watched. Windows answers the same question a different way: the volume's
drive type.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

#: Read once, and read through a name rather than in each test, because mypy narrows a literal
#: `sys.platform` comparison and then calls the other platform's branch unreachable.
_WINDOWS = sys.platform == "win32"

#: Filesystems that reach another machine over a network.
#:
#: Deliberately a list of what IS remote rather than what is local: a filesystem nobody here has
#: heard of is far more likely to be a local one (a new copy-on-write filesystem, an overlay, a
#: container runtime's own) than a new network protocol. Guessing "remote" for an unknown name
#: would put a local folder on a timer and stop it noticing files immediately, which is the worse
#: of the two mistakes: the other way round, a share is simply watched like a local folder.
REMOTE_FILESYSTEMS = frozenset(
    {
        "9p",
        "afs",
        "ceph",
        "cifs",
        "coda",
        "fuse.sshfs",
        "fuse.rclone",
        "fuse.s3fs",
        "fuseblk",
        "ftpfs",
        "gfs2",
        "glusterfs",
        "lustre",
        "ncpfs",
        "nfs",
        "nfs4",
        "smb2",
        "smb3",
        "smbfs",
        "sshfs",
    }
)

_MOUNTS = Path("/proc/self/mounts")

#: `GetDriveTypeW`'s answer for a drive served by another machine: a mapped letter or a UNC
#: path. The other values (fixed, removable, CD, RAM disk, and the two failure codes) are all
#: either local or unknown, and unknown reads as local for the reason in `is_remote`.
_DRIVE_REMOTE = 4


@dataclass(frozen=True, slots=True)
class Mount:
    """One row of the mount table: where it is attached, what it is, and what is behind it.

    Three fields, because two different questions are asked of the same row (which filesystem a
    path is on, and what the thing carrying it is called), and
    parsing the file twice to answer them separately is how the two would eventually disagree about
    which row covers a path.
    """

    #: Where it is attached, e.g. `/media/photos`.
    point: str
    #: The filesystem type, lowercased, e.g. `ext4` or `cifs`.
    kind: str
    #: What is mounted there, e.g. `/dev/sda2` or `//nas/media`.
    source: str


def _mount_table() -> list[Mount]:
    """Every mount point on this machine and what is behind it, longest path first.

    Longest first because mounts nest: `/media` may be local while `/media/photos` is a share, and
    the answer for a path under the second is the second. Sorting once here means the lookup below
    is the first match rather than a search for the best one.
    """
    try:
        raw = _MOUNTS.read_text(encoding="utf-8", errors="replace")
    except OSError:
        # No mount table: a platform that does not publish one, or a sandbox that hides it.
        # Treated as "cannot tell", which the caller reads as local.
        return []
    found: list[Mount] = []
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        # The mount point is escaped in this file: a space is \040, a tab \011.
        point = parts[1].replace("\\040", " ").replace("\\011", "\t").replace("\\012", "\n")
        found.append(Mount(point=point, kind=parts[2].lower(), source=parts[0]))
    found.sort(key=lambda mount: len(mount.point), reverse=True)
    return found


def _covering_mount(path: Path) -> Mount | None:
    """The mount table row a directory sits under, or None when there is no telling.

    THE shared question, asked once. Both things this module answers about a POSIX path (which
    filesystem it is on and what the volume carrying it is called) are read off the same row, and
    two searches for "the best matching mount point" would be free to pick different rows.

    Resolved first, because a symlink into a share is a path on that share, and a folder somebody
    picks through the browser is as likely to be a link as not.

    The comparison is in forward slashes, not in the local separator. The file being read always
    writes them that way whatever platform is reading it, so `/` is a property of the input rather
    than of the host, and on POSIX, where this actually runs, the two are the same character
    anyway.
    """
    try:
        target = path.resolve()
    except OSError:
        return None
    as_text = str(target).replace(os.sep, "/")
    for mount in _mount_table():
        if (
            as_text == mount.point
            or as_text.startswith(mount.point.rstrip("/") + "/")
            or mount.point == "/"
        ):
            return mount
    return None


def _volume_root(path: Path) -> str | None:
    """The volume a Windows path sits on, as its root: `C:\\`, or a share's `\\\\server\\name\\`.

    Its own function because it is a question with its own edge cases (a junction, a path
    that has just gone, a name too long for the buffer) and `_drive_type` should not have to hold
    them as well as its own.

    None when Windows will not say, which every caller reads as "cannot tell".
    """
    import ctypes

    try:
        # `WinDLL` is absent from ctypes on other platforms, so the ignore is needed when this is
        # checked on Linux and unnecessary when it is checked here. Both codes, so neither host
        # complains about the other.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined, unused-ignore]

        volume = ctypes.create_unicode_buffer(261)
        if not kernel32.GetVolumePathNameW(str(path), volume, len(volume)):
            return None
        return volume.value or None
    # `ctypes.ArgumentError` is NOT a subclass of ValueError, and a path holding a NUL byte
    # raises exactly that, so a guard listing only the three obvious errors would let it through
    # and the whole call would raise where it is supposed to answer None.
    except (OSError, AttributeError, ValueError, ctypes.ArgumentError):
        return None


def _drive_type(path: Path) -> int | None:
    """What Windows says the volume under a path is: fixed, removable, remote, or it will not say.

    One call answers both shapes this has to handle. A UNC path (`\\\\nas\\media`) is
    remote by construction, and a mapped drive letter is remote because the mapping is to a share,
    and `GetDriveTypeW` reports `DRIVE_REMOTE` for each of them, so neither needs its own special
    case and neither can grow its own bug.

    A mapped letter is nonetheless the wrong thing to STORE. A mapping belongs to one logged-in
    session and exists only once Windows has reconnected it, so an application that starts before
    that (or under any account that does not have it) sees a whole share as missing files. Sift
    stores the UNC path; this is what tells the truth about either one if it meets it.

    None when the question cannot be asked at all, which `is_remote` reads as local. It is kept as
    its own function so the decision above it is testable on either platform without a Windows
    kernel to call into.
    """
    import ctypes

    root = _volume_root(path)
    if root is None:
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined, unused-ignore]
        return int(kernel32.GetDriveTypeW(root))
    except (OSError, AttributeError, ValueError, ctypes.ArgumentError):
        return None


def filesystem_of(path: Path) -> str | None:
    """The filesystem a directory is mounted from, or None if it cannot be told.

    POSIX only, and it says so by answering None everywhere else: Windows publishes no equivalent
    of a mount table, and inventing a filesystem name for it would put a string nothing else in
    Sift knows how to read into the one place this module is consulted from.
    """
    mount = _covering_mount(path)
    return None if mount is None else mount.kind


@dataclass(frozen=True, slots=True)
class Storage:
    """The volume a path sits on, and whether another machine serves it.

    `key` names the storage the way the operating system does (`C:\\` or `\\\\server\\share\\`
    on Windows, a mount point on POSIX), so two paths that share a disk or a share share a key,
    and two that do not, do not. `remote` is `is_remote`'s answer, kept beside the key so the two
    are read off the same resolution of the same path.
    """

    key: str
    remote: bool


def storage_of(path: Path) -> Storage:
    """Which storage a path is on. Local, and the path's own drive or root, when nothing will say.

    A mapped drive letter is resolved to the share behind it first, so `S:\\photos` and
    `\\\\nas\\photos` are one storage rather than two with the same disks behind them, and so
    the answer does not depend on which of the two ways the folder happened to be picked.
    """
    if _WINDOWS:
        try:
            target = path.resolve()
        except OSError:
            target = path
        root = _volume_root(target)
        if root is None:
            drive = os.path.splitdrive(str(target))[0]
            return Storage(key=(drive + os.sep) if drive else os.sep, remote=False)
        return Storage(key=root, remote=_drive_type(target) == _DRIVE_REMOTE)

    mount = _covering_mount(path)
    if mount is None:
        return Storage(key="/", remote=False)
    return Storage(key=mount.point, remote=is_remote(path))


def is_remote(path: Path) -> bool:
    """Whether a directory is served from another machine.

    False when it cannot be told. A folder wrongly treated as local is watched like any local
    folder; one wrongly treated as remote is quietly put on a timer and
    stops noticing new files immediately, which is a fault somebody would have to guess at.
    """
    if _WINDOWS:
        try:
            target = path.resolve()
        except OSError:
            return False
        # Remote, or nothing. Every other drive type is local, and so is "Windows would not say",
        # for the reason in the docstring above: a folder wrongly called local is watched like any
        # local folder.
        return _drive_type(target) == _DRIVE_REMOTE

    kind = filesystem_of(path)
    if kind is None:
        return False
    if kind in REMOTE_FILESYSTEMS:
        return True
    # FUSE drivers name themselves after the dot: fuse.sshfs, fuse.rclone. A bare "fuse" says
    # nothing about where the data is, so it is not treated as remote.
    return kind.startswith("fuse.") and kind in REMOTE_FILESYSTEMS
