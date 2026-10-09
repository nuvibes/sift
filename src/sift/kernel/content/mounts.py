# SPDX-License-Identifier: AGPL-3.0-or-later
"""Whether a directory lives on this machine or another, so a share is polled on a timer."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

#: A name, as mypy calls a literal `sys.platform` comparison's other branch unreachable.
_WINDOWS = sys.platform == "win32"

#: What is remote, not what is local: an unknown filesystem is far likelier to be local.
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

#: Every other drive type is local or unknown, and unknown reads as local.
_DRIVE_REMOTE = 4


@dataclass(frozen=True, slots=True)
class Mount:
    """One row of the mount table, parsed once for both questions asked of it."""

    point: str
    kind: str
    source: str


def _mount_table() -> list[Mount]:
    """Every mount point and what is behind it, longest first since mounts nest."""
    try:
        raw = _MOUNTS.read_text(encoding="utf-8", errors="replace")
    except OSError:
        # No mount table: read as cannot tell, which the caller treats as local.
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
    """The mount row a resolved directory sits under, or None when there is no telling."""
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
    """The volume a Windows path sits on, as its root, or None when Windows will not say."""
    import ctypes

    try:
        # Both codes, so neither the Linux nor the Windows check complains.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined, unused-ignore]

        volume = ctypes.create_unicode_buffer(261)
        if not kernel32.GetVolumePathNameW(str(path), volume, len(volume)):
            return None
        return volume.value or None
    # `ctypes.ArgumentError` is not a ValueError, and a NUL in a path raises it.
    except (OSError, AttributeError, ValueError, ctypes.ArgumentError):
        return None


def _drive_type(path: Path) -> int | None:
    """What Windows says the volume under a path is, or None; mapped and UNC both read remote."""
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
    """The filesystem a directory is mounted from, or None if it cannot be told (Windows)."""
    mount = _covering_mount(path)
    return None if mount is None else mount.kind


@dataclass(frozen=True, slots=True)
class Storage:
    """The volume a path sits on, named as the system names it, and whether it is remote."""

    key: str
    remote: bool


def storage_of(path: Path) -> Storage:
    """Which storage a path is on, a mapped letter resolved to its share; local when unknown."""
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
    """Whether a directory is served from another machine; False when it cannot be told."""
    if _WINDOWS:
        try:
            target = path.resolve()
        except OSError:
            return False
        # Remote, or nothing: a folder wrongly called local is still watched.
        return _drive_type(target) == _DRIVE_REMOTE

    kind = filesystem_of(path)
    if kind is None:
        return False
    if kind in REMOTE_FILESYSTEMS:
        return True
    # A bare "fuse" says nothing about where the data is.
    return kind.startswith("fuse.") and kind in REMOTE_FILESYSTEMS
