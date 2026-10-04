# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shapes a write into somebody's library is described by, the refusal it can end in, and the
one place that decides whether it is allowed at all.

THE PERMISSION CHECK LIVES HERE AND NOWHERE ELSE.

The backend runs as the logged-in user with write access to every drive, so nothing outside these
functions stands between a mistake and somebody's library: no read-only mount turns the syscall
down. A flag makes an error forbidden, not impossible. That is accepted knowingly, and it is exactly
why the check may exist in one place and not two.

Two correct copies of one rule is the shape that ends with one of them being updated, so
`tests/gates/test_one_write_door.py` fails the build if a second copy appears.

Sift indexes files where they are. The exceptions (deleting one, renaming one, producing a new
one beside an existing one, and arranging the folders they sit in) go through a single fenced
service, and features that PRODUCE files reach it through a seam rather than by importing it.

These live here rather than beside that seam because the seams package is interfaces and nothing
else: a Protocol has no body, and a dataclass or an exception does. So the interface is over there
and the vocabulary it speaks is here, which is also the right place for it: the refusal is caught
by callers that never touch the seam, and the two shapes describe a write whoever performs it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from sift.kernel.access import ObjectType, Repository
from sift.kernel.content.library import LibraryError, LibraryStore, Root, check_folder_writable
from sift.kernel.filenames import InvalidFilename, check_folder_name


class LibraryWriteRefused(Exception):
    """A write into a library folder will not be carried out, and the message says why.

    Defined in the kernel rather than in the feature that raises it, so a feature producing a file
    can catch a refusal without importing the feature that decides on one. The sentence is written
    for the person who pressed the button; callers pass it through unchanged.
    """


@dataclass(frozen=True, slots=True)
class Staged:
    """A name claimed in a library folder, and a scratch path to build the file at.

    `working` is where the bytes are written; `filename` is what the finished file will be called.
    The two are in the same folder on purpose: putting the finished file in place is then a
    rename within one directory, which is instant and cannot half-succeed. Building somewhere else
    and copying in would need the space twice, and would fail outright when the scratch area and
    the library sit on different disks.

    `working` deliberately does not end in a media extension, so the folder scan walks straight
    past it. A half-written video carrying a real extension is a file the scan will try to take in
    while it is still being written.
    """

    asset_id: str
    location_id: str
    root_id: str
    folder_id: str | None
    rel_path: str
    filename: str
    working: Path


@dataclass(frozen=True, slots=True)
class Placed:
    """Where a produced file ended up, once it was really there."""

    root_id: str
    folder_id: str | None
    rel_path: str
    filename: str
    path: Path


async def forget_folder(library: LibraryStore, access: Repository, folder_id: str) -> bool:
    """Take a folder and everything under it out of the library, and every permission naming it.

    Touches no file. Two stores have to agree for a folder to be gone, and the ORDER between them is
    the whole of what this function is: the grants go first. `acl_grants.object_id` carries no
    foreign key, so nothing cascades them, and a grant left behind after the folder row is gone
    names an id that a later folder can be given, which is a share nobody granted.
    Forget-then-delete leaves a grant naming a folder that has already gone, which is inert.

    In the kernel, and taking both stores, because two features now need it and neither may import
    the other: the scan calls it when a walk finds a directory that is no longer there, and the
    delete feature calls it when it has just removed one. Written twice, one copy would be the one
    that keeps the rule and the other would be the one somebody updates.
    """
    folder = await library.get_folder(folder_id)
    if folder is None:
        # Already gone: an ancestor took it by cascade. Not a failure: the outcome asked for is
        # the outcome there is.
        return False
    for inside in await library.folders_under(folder):
        await access.forget_object(ObjectType.FOLDER, inside.id)
    await access.forget_object(ObjectType.FOLDER, folder.id)
    return await library.remove_folder(folder.id)


async def require_root(library: LibraryStore, root_id: str) -> Root:
    """The root a file belongs to, refused as a sentence when it is no longer part of the library.

    Its own function, and in the kernel, because both features that change files need it and both
    need it to fail the same way. A caller that fetched the root itself would either restate this
    sentence or read an attribute off `None` and hand somebody an
    `AttributeError` where a sentence was owed.
    """
    root = await library.get_root(root_id)
    if root is None:
        raise LibraryWriteRefused("The folder this file was in is no longer part of your library.")
    return root


async def check_folder_may_change(directory: Path) -> None:
    """That the filesystem allows Sift to write in this folder, asked at the moment of the write.

    A folder made read-only since it was added is refused here, in a sentence, rather than by the
    unlink or the rename failing halfway through the operation.

    No per-folder consent flag is read: handing Sift a folder is the permission, and the protection
    is the confirmation at the moment of a destructive act. What is checked is the fact, which is
    all a check can honestly assert.

    Takes the folder about to be written INTO, never a file inside it: a caller holding the
    directory must not hand over its PARENT, or the check passes on the folder above the one being
    written to.

    Blocking (it reads the disk), so off the loop, like every other filesystem question here.
    """
    try:
        await asyncio.to_thread(check_folder_writable, directory)
    except LibraryError as refusal:
        raise LibraryWriteRefused(str(refusal)) from refusal


async def check_may_change(directory: Path) -> None:
    """The ordinary way in, for a caller with one folder to write into.

    The same question as `check_folder_may_change`, under the name every caller uses. A caller
    writing into TWO folders (a move has a source and a destination) asks about each.
    """
    await check_folder_may_change(directory)


async def create_directory(directory: Path, name: str) -> Path:
    """Make one folder inside a library, or refuse in a sentence somebody can act on.

    Here rather than in the feature that offers it, because making a folder inside somebody's
    library is changing their library: the same permission a delete or a rename needs, asked the
    same way, in the one place that may ask it.

    **One level, never a path.** `name` is checked as a single segment, so it cannot walk out of the
    folder the permission was resolved against, and the folder it lands in is confined against the
    root before anything is created. Two guards for one property, because the first is about what
    somebody typed and the second is about where the row said the folder was.

    Refuses a name that is already taken rather than quietly handing back what is there. Making a
    folder that already exists is not the operation somebody asked for, and answering as though it
    worked is how two people end up believing they each made it.
    """
    await check_folder_may_change(directory)
    try:
        wanted = check_folder_name(name)
    except InvalidFilename as refusal:
        raise LibraryWriteRefused(str(refusal)) from refusal

    made = directory / wanted
    # `mkdir` without `exist_ok` is what actually makes this safe: it is one syscall and cannot
    # be raced. This check is for the SENTENCE: without it the refusal is the operating system's
    # ("File exists"), which does not say what is already there or what to do about it.
    if await asyncio.to_thread(made.exists):
        raise LibraryWriteRefused(f'There is already something called "{wanted}" in that folder.')
    try:
        await asyncio.to_thread(made.mkdir)
    except OSError as failure:
        raise LibraryWriteRefused(
            f'Sift could not make a folder called "{wanted}" there ({failure.strerror}).'
        ) from failure
    return made


async def move_directory(source: Path, destination: Path) -> None:
    """Rename a folder, or move one, inside a library. Both are the same act to a filesystem.

    **Sift renaming a folder is not the same as Sift giving a folder a second name.** A name kept
    beside the disk's own drifts the moment somebody renames the
    directory in their file manager; this changes both at once, because it changes the directory.

    Both ends are checked. A move has a source folder and a destination folder and either can be
    somewhere the filesystem refuses, so it is asked about each.

    Refuses a destination inside the source, which the filesystem would either refuse confusingly
    or (on some of them) accept, producing a folder inside itself and a tree with no bottom.
    """
    await check_folder_may_change(source.parent)
    await check_folder_may_change(destination.parent)

    if source == destination:
        return
    if source in destination.parents:
        raise LibraryWriteRefused("A folder cannot be moved inside itself.")
    if await asyncio.to_thread(destination.exists):
        raise LibraryWriteRefused(
            f'There is already something called "{destination.name}" in that folder.'
        )
    try:
        # `os.replace` is deliberately NOT used. On Windows it will replace a FILE with a
        # DIRECTORY without complaint, which POSIX refuses, so the one call that looks like the
        # careful choice is the one that can quietly destroy something. The existence check above
        # is what stands in for it, and `rename` refuses rather than overwrites.
        await asyncio.to_thread(source.rename, destination)
    except OSError as failure:
        raise LibraryWriteRefused(
            f"Sift could not move that folder ({failure.strerror})."
        ) from failure
