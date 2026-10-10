# SPDX-License-Identifier: AGPL-3.0-or-later
"""Listing the folders handed to Sift, so one can be picked without typing it.
Directories only, every path confined inside a grant, and nothing is ever written."""

from __future__ import annotations

import asyncio
import fnmatch
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sift.kernel.paths import PathEscape, confine, is_writable, mount_is_readonly


@dataclass(frozen=True, slots=True)
class Entry:
    """One directory somebody could pick, as the picker needs to draw it."""

    name: str
    path: str


@dataclass(frozen=True, slots=True)
class Listing:
    """What is inside one directory, and how to get back out of it."""

    path: str
    entries: tuple[Entry, ...]
    breadcrumb: tuple[Entry, ...]
    #: A count, never names.
    file_count: int
    nothing_granted: bool
    #: Decided by the mount and by Sift's own account's write permission.
    writable: bool
    #: Told apart from `writable`: the way out of each is a different sentence.
    read_only_mount: bool
    #: The files whose names match what the chooser asked for; never any other file's name.
    files: tuple[Entry, ...] = ()


class BrowseRefused(Exception):
    """The path asked for is not somewhere the picker will look."""


def _entry(path: Path) -> Entry:
    """A directory as the picker draws it. A drive or share has no name, so its path stands in."""
    return Entry(name=path.name or str(path).rstrip("\\/") or str(path), path=str(path))


def _breadcrumb(root: Path, here: Path) -> tuple[Entry, ...]:
    """The way back out, outermost first, stopping at the media root."""
    crumbs = [_entry(root)]
    parts = here.relative_to(root).parts if here != root else ()
    for depth in range(len(parts)):
        crumbs.append(_entry(root.joinpath(*parts[: depth + 1])))
    return tuple(crumbs)


@dataclass(frozen=True, slots=True)
class _Contents:
    """What one directory turned out to hold."""

    entries: tuple[Entry, ...]
    files: int
    named: tuple[Entry, ...] = ()


def _read(here: Path, names: Sequence[str] = ()) -> _Contents:
    """The subdirectories of one directory, in reading order, a count of its files, and the files
    whose names match one of `names` (patterns, any case). No other file is named.
    Symlinked directories are not followed; an unreadable directory lists as empty."""
    found: list[Entry] = []
    named: list[Entry] = []
    files = 0
    patterns = [name.casefold() for name in names]
    try:
        with os.scandir(here) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    found.append(_entry(Path(entry.path)))
                    continue
                files += 1
                if any(fnmatch.fnmatchcase(entry.name.casefold(), one) for one in patterns):
                    named.append(_entry(Path(entry.path)))
    except OSError:
        return _Contents(entries=(), files=0)
    return _Contents(
        entries=tuple(sorted(found, key=lambda entry: entry.name.casefold())),
        files=files,
        named=tuple(sorted(named, key=lambda entry: entry.name.casefold())),
    )


def _resolve_all(roots: Sequence[Path]) -> list[tuple[Path, Path]]:
    """Each granted folder as (stored, resolved), skipping the ones unreachable right now."""
    found: list[tuple[Path, Path]] = []
    for root in roots:
        try:
            found.append((root, root.resolve(strict=True)))
        except OSError:
            continue
    return found


def _top(roots: Sequence[Path]) -> Listing:
    """The first thing the picker shows: every granted folder, reachable or not."""
    entries = tuple(_entry(root) for root in sorted(roots, key=lambda one: str(one).casefold()))
    return Listing(
        path="",
        entries=entries,
        breadcrumb=(),
        file_count=0,
        nothing_granted=not entries,
        writable=False,
        read_only_mount=False,
    )


def _look(roots: Sequence[Path], requested: Path | None, names: Sequence[str] = ()) -> Listing:
    """The blocking half: resolve, confine, and read. Runs in a thread."""
    if requested is None:
        return _top(roots)

    reachable = _resolve_all(roots)

    # The first grant containing it wins; one sentence for every kind of path outside them all.
    here: Path | None = None
    for _stored, resolved_root in reachable:
        try:
            here = confine(resolved_root, requested)
            break
        except PathEscape:
            continue

    if here is None:
        raise BrowseRefused(
            "That folder isn't one of the places Sift has been given. Sift can only look inside "
            "the folders that were handed to it."
        )

    if not here.is_dir():
        raise BrowseRefused("That isn't a folder.")

    contents = _read(here, names)
    return Listing(
        path=str(here),
        entries=contents.entries,
        breadcrumb=_breadcrumb(_resolved_root_of(here, reachable), here),
        file_count=contents.files,
        nothing_granted=False,
        writable=is_writable(here),
        read_only_mount=mount_is_readonly(here),
        files=contents.named,
    )


def _resolved_root_of(here: Path, reachable: Sequence[tuple[Path, Path]]) -> Path:
    """Which granted folder a path was proved inside, so the breadcrumb stops there."""
    for _stored, resolved_root in reachable:
        if here == resolved_root or resolved_root in here.parents:
            return resolved_root
    # `here` came out of `confine`, so this is unreachable.
    return here  # pragma: no cover


def _machine_roots() -> list[Path]:
    r"""Every place a walk of this computer can start: the drives, or `/`; unready drives are left
    out."""
    if sys.platform != "win32":
        return [Path("/")]
    try:
        lettered = os.listdrives()
    except OSError:
        return []
    found: list[Path] = []
    for drive in lettered:
        where = Path(drive)
        try:
            if where.is_dir():
                found.append(where)
        except OSError:
            continue
    return found


async def machine_roots() -> list[Path]:
    """`_machine_roots` off the event loop: a not-ready drive can take seconds to answer."""
    return await asyncio.to_thread(_machine_roots)


async def look(roots: Sequence[Path], requested: Path | None, names: Sequence[str] = ()) -> Listing:
    """What is inside `requested`, proved inside a granted folder; with none, the granted folders.
    `names` are the patterns of the files to name as well, for a chooser of a file."""
    return await asyncio.to_thread(_look, roots, requested, names)


__all__ = ["BrowseRefused", "Entry", "Listing", "look", "machine_roots"]
