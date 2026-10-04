# SPDX-License-Identifier: AGPL-3.0-or-later
"""Catching up at start: which folders moved while Sift was closed, and what changed in each."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from sift.kernel.content import (
    FolderRow,
)
from sift.kernel.log import get_logger
from sift.kernel.paths import PathEscape, confine
from sift.slices.library_roots.walking import (
    WORTH_OPENING as WORTH_OPENING,
)

if TYPE_CHECKING:
    from sift.slices.library_roots.sweeping import _Item

log = get_logger(__name__)


def _folders_that_moved(
    base: Path, folders: Sequence[FolderRow]
) -> tuple[list[tuple[FolderRow, float]], set[str]]:
    """Which folders' directories have moved since a scan last looked. Blocking.

    On a thread: one `stat` per folder over what may be a network share. A folder whose directory
    will not answer comes back as a WALK of its parent: only the parent's listing can tell a folder
    removed from one renamed."""
    moved: list[tuple[FolderRow, float]] = []
    walk: set[str] = set()
    for folder in folders:
        try:
            directory = confine(base, base / folder.rel_path) if folder.rel_path else base
        except PathEscape:
            continue
        try:
            now = directory.stat().st_mtime
        except OSError:
            if folder.parent_id is not None:
                walk.add(folder.parent_id)
            continue
        # NULL reads as "cannot be ruled out": a folder no pass has recorded.
        if folder.seen_mtime is not None and now == folder.seen_mtime:
            continue
        # The timestamp comes back with the row, so what is recorded afterwards is the value this
        # pass DECIDED ON, never a later one it did not look at.
        moved.append((folder, now))
    return moved, walk


def _differences(
    base: Path, folder: FolderRow, recorded: set[_Item], known: set[str]
) -> tuple[set[str], bool]:
    """What one changed folder holds that Sift's rows do not say, and whether its shape changed.

    ONLY the differences: the files which did not move are never mentioned again.

    Compared on `(name, size)`, which is what `_recorded_in` already returns and what a directory
    entry already carries on the site Sift ships on, so a listing costs no more than the names.
    A recorded size of None is an archive, whose row stands for the pictures inside it rather than
    for a file of its own, matched on the name alone, or every archive would look changed for ever.

    `structural` is True when the folder holds a directory Sift has no row for. That cannot be
    answered by naming files: a new subtree needs walking, and a renamed one needs its listing
    compared so the folder can be RECOGNISED rather than replaced."""
    try:
        directory = confine(base, base / folder.rel_path) if folder.rel_path else base
        entries = list(os.scandir(directory))
    except (OSError, PathEscape):
        return set(), False

    sizes: dict[str, int | None] = {name: size for name, size in recorded}
    listed: dict[str, int] = {}
    structural = False
    for entry in entries:
        try:
            if entry.is_junction():
                continue
            if entry.is_dir(follow_symlinks=False):
                inside = f"{folder.rel_path}/{entry.name}" if folder.rel_path else entry.name
                if inside not in known:
                    structural = True
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            if Path(entry.name).suffix.lower() not in WORTH_OPENING:
                continue
            listed[entry.name] = entry.stat(follow_symlinks=False).st_size
        except OSError:
            continue

    differing = {
        name
        for name, size in listed.items()
        # Arrived, or the same name over different bytes. An archive's recorded size is None and is
        # matched on the name alone.
        if name not in sizes or (sizes[name] is not None and sizes[name] != size)
    }
    # And what Sift believes is there and is not. Named too: `look_at` leaves out a path that has
    # gone and the sweep, filtered to these, is what marks it missing.
    differing |= {name for name in sizes if name not in listed}

    prefix = f"{folder.rel_path}/" if folder.rel_path else ""
    return {prefix + name for name in differing}, structural
