# SPDX-License-Identifier: AGPL-3.0-or-later
"""Looking at the folders that have been handed to Sift, so one can be picked without typing it.

Somebody who keeps their videos on a drive of their own should not have to type where it is. So
this lists what is there instead.

    it starts at the folders somebody GAVE Sift, not at the top of the disk. The backend
                                          runs as the logged-in user with every drive in reach, so
                                          nothing outside Sift confines it, and the grant list is
                                          what does: a folder
                                          gets into that list by somebody choosing it in Windows'
                                          own dialog, which no page can open, drive or read
    every path is proved inside one       by the same confinement the rest of Sift uses, which
                                          resolves the path and follows every link before deciding.
                                          A `..`, an absolute path, and a symlink pointing out are
                                          refused by one check, not three
    directories only                     a file is not listed, so this cannot be turned into a
                                          way of learning what somebody has named their files,
                                          and it never opens one
    it reads and nothing else            no request here creates, moves or deletes anything

It is admin-only, which is enforced by the route rather than here. That is a second lock on a door
that is already narrow: a listing of somebody's folders is a description of the machine Sift runs
on, and the people who may see it are the people who set it up.
"""

from __future__ import annotations

import asyncio
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
    """What is inside one directory, and how to get back out of it.

    `nothing_granted` means there is nothing for Sift to index yet, and the interface answers it
    with the explanation of how to hand a folder over rather than with an empty list. It is
    deliberately narrow: true only at the top of the picker, and only when no folder has been
    granted at all. An empty folder further in is ordinary, and a granted folder that is offline
    right now (a NAS that is not answering) is still listed, because hiding it would be telling
    somebody their folder is gone when it is their network that is.
    """

    path: str
    entries: tuple[Entry, ...]
    breadcrumb: tuple[Entry, ...]
    #: How many files are in this directory. Not their names: see `_read` for why a count is the
    #: whole of what this says, and why it is the directory's own rather than each subfolder's.
    file_count: int
    #: Nobody has handed Sift a folder yet, so there is nothing to pick and the answer is the
    #: explanation of how to hand one over.
    nothing_granted: bool
    #: Whether Sift could change files here, were it asked to. Decided by the mount and by whether
    #: Sift's own account may write, because either one alone will let somebody tick a box that
    #: cannot do what it says.
    writable: bool
    #: Told apart from `writable` so the interface can say which of the two it is. The way out of
    #: a read-only mount is a different sentence from the way out of a permissions problem.
    read_only_mount: bool


class BrowseRefused(Exception):
    """The path asked for is not somewhere the picker will look."""


def _entry(path: Path) -> Entry:
    """A directory as the picker sees it: what it is called, and where it is.

    The path goes back to the client, which is the one place in this feature that happens. It has
    to: the picker's whole job is to hand a path to the endpoint that adds a root, and a name with
    no path is not something that endpoint can be given. It is admin-only and it never leaves the
    media area, which is what makes that acceptable rather than a leak of the server's layout.

    A root directory has no name of its own (`Path('/media').name` is `media`, but a root like
    `/` has nothing, and neither does a UNC share, `\\\\nas\\Media`), so the path stands in
    for it, with its trailing separator taken off. Left on, the picker would draw a granted NAS
    share as `\\\\nas\\Media\\`, which reads as an unfinished path.
    """
    return Entry(name=path.name or str(path).rstrip("\\/") or str(path), path=str(path))


def _breadcrumb(root: Path, here: Path) -> tuple[Entry, ...]:
    """The way back out, outermost first, stopping at the media root.

    The root is always the first crumb, so somebody who has clicked down four folders can always
    get back to the top. Nothing above the root is a crumb: it is not somewhere this will list, so
    offering it as a link would be offering a refusal.
    """
    crumbs = [_entry(root)]
    parts = here.relative_to(root).parts if here != root else ()
    for depth in range(len(parts)):
        crumbs.append(_entry(root.joinpath(*parts[: depth + 1])))
    return tuple(crumbs)


@dataclass(frozen=True, slots=True)
class _Contents:
    """What one directory turned out to hold.

    A count of files and never their names (see `_read` for why). Nothing here records "there was
    nothing at all in it": the "hand Sift a folder" explanation is decided by whether any folder has
    been GRANTED rather than by whether one happens to be empty. An empty folder somebody chose on
    purpose is an empty folder, not a missing setup step.
    """

    entries: tuple[Entry, ...]
    #: How many entries were not directories. A number, never a name (see `_read`).
    files: int


def _read(here: Path) -> _Contents:
    """The subdirectories of one directory, in the order a person reads them, and how many files.

    `os.scandir` answers "is this a directory" from what it already read, without a second call
    per entry. `follow_symlinks=False` is what stops a symlinked directory being listed as a
    folder here: following it would show, under the media root, a folder that is somewhere else
    entirely, and the whole point of this module is that everything it shows is inside the root.

    **The count is a number and never a list.** Without it a folder holding files but no
    subfolders would draw an empty picker, which reads as "there is nothing here", so somebody who
    had pointed Sift straight at their library would see a screen telling them it was empty. Saying
    how many files are here answers that without listing them, which keeps the rule this module is
    built on: this is not a way to learn what somebody has named their files.

    It is THIS directory's count and not one per subfolder, which is a cost decision. Counting each
    subfolder means opening each of them: one extra directory read per row, on a network share,
    every time somebody clicks. The number that answers the confusion is the one for the folder
    being looked at, and it is free: the scan is already happening.

    A directory that cannot be read lists as empty rather than raising: a granted folder with one
    unreadable folder in it should still show the other nine.
    """
    found: list[Entry] = []
    files = 0
    try:
        with os.scandir(here) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    found.append(_entry(Path(entry.path)))
                else:
                    files += 1
    except OSError:
        return _Contents(entries=(), files=0)
    return _Contents(
        entries=tuple(sorted(found, key=lambda entry: entry.name.casefold())),
        files=files,
    )


def _resolve_all(roots: Sequence[Path]) -> list[tuple[Path, Path]]:
    """Each granted folder as (what was stored, what it resolves to), skipping the unreachable.

    A grant whose folder cannot be resolved right now is not dropped from the picker (see `_top`).
    It is dropped from the set a requested path is proved against, because there is nothing to prove
    against: `confine` needs a real directory, and a folder that is not there cannot contain
    anything.
    """
    found: list[tuple[Path, Path]] = []
    for root in roots:
        try:
            found.append((root, root.resolve(strict=True)))
        except OSError:
            continue
    return found


def _top(roots: Sequence[Path]) -> Listing:
    """The first thing the picker shows: the folders somebody has handed to Sift.

    Every grant is listed, reachable or not. A NAS that is asleep, a drive that is unplugged, a
    share whose network is down: all of those are temporary, and hiding the folder would tell
    somebody their library had disappeared when what had disappeared was their network. Clicking
    into one is refused by `confine` at that point, with a sentence.

    Each row carries its last component as the name AND its whole path, which is what `_entry`
    already does for every other row in the picker. The screen shows the path underneath: two
    granted folders can easily end in the same word (`D:\\media` and `\\\\nas\\media`), and two rows
    both called "media" with nothing to tell them apart is asking somebody to guess. Deciding that
    here (by jamming the path into the name) would put a layout decision in the server and give
    rows reading `/media/library`.
    """
    entries = tuple(_entry(root) for root in sorted(roots, key=lambda one: str(one).casefold()))
    return Listing(
        path="",
        entries=entries,
        breadcrumb=(),
        file_count=0,
        # Nothing has been handed over at all. Not "the folder is empty" and not "it is unreachable":
        # only this one means the answer is the explanation of how to give Sift a folder.
        nothing_granted=not entries,
        writable=False,
        read_only_mount=False,
    )


def _look(roots: Sequence[Path], requested: Path | None) -> Listing:
    """The blocking half: resolve, confine, and read. Runs in a thread."""
    if requested is None:
        return _top(roots)

    reachable = _resolve_all(roots)

    # Proved against every granted folder, and the first one that contains it wins. A path that is
    # inside none of them is refused in one sentence whether it got there by `..`, by naming
    # somewhere else outright, or through a symlink: the three are the same request, and telling
    # them apart would describe the machine to whoever was trying them.
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

    contents = _read(here)
    return Listing(
        path=str(here),
        entries=contents.entries,
        breadcrumb=_breadcrumb(_resolved_root_of(here, reachable), here),
        file_count=contents.files,
        # Only ever true at the top of the picker. Somewhere inside a granted folder, an empty
        # directory is an empty directory.
        nothing_granted=False,
        writable=is_writable(here),
        read_only_mount=mount_is_readonly(here),
    )


def _resolved_root_of(here: Path, reachable: Sequence[tuple[Path, Path]]) -> Path:
    """Which granted folder a path was proved inside, so the breadcrumb stops there.

    The breadcrumb must not offer a link above the grant: that is not somewhere this will list, so
    offering it would be offering a refusal.
    """
    for _stored, resolved_root in reachable:
        if here == resolved_root or resolved_root in here.parents:
            return resolved_root
    # Unreachable in practice: `here` came out of `confine` against one of these. Answering with
    # the path itself gives a one-crumb breadcrumb rather than raising on the way to drawing a page.
    return here  # pragma: no cover


def _machine_roots() -> list[Path]:
    r"""Every place a walk of THIS COMPUTER can start: the drives, or `/`.

    WHY THIS EXISTS AT ALL. The operating system's own folder dialog can only be opened by the
    application on the machine itself, so without this a Sift reached from another device could
    look inside folders already handed over and no further, with no way to point it at a new one
    without walking to the computer.

    WHAT IT DOES NOT CHANGE. The permission was already there: an administrator's session has
    always been able to hand Sift any path it names, from any device, and this only makes that
    reachable without knowing how to spell the path. Nothing here reads a file, nothing here lists
    one, and nothing here grants anything: it answers which folders exist, and granting is still
    its own deliberate step.

    An unreadable or not-ready drive is left out rather than raised: an empty card reader is not an
    error, and a list that refuses to answer because of one is worse than a list without it.
    """
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


async def look(roots: Sequence[Path], requested: Path | None) -> Listing:
    """What is inside `requested`, having proved it is inside one of the granted folders.

    With no `requested`, the granted folders themselves. Every path this returns is one the same
    call will accept back, which is what lets the picker walk down without the client ever
    assembling a path of its own.
    """
    return await asyncio.to_thread(_look, roots, requested)


__all__ = ["BrowseRefused", "Entry", "Listing", "look", "machine_roots"]
