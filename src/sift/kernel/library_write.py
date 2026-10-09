# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one place that decides whether a write into somebody's library is allowed.

A second copy of the check is refused by `tests/gates/test_one_write_door.py`."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from sift.kernel.access import ObjectType, Repository
from sift.kernel.content.library import LibraryError, LibraryStore, Root, check_folder_writable
from sift.kernel.filenames import InvalidFilename, check_folder_name


class LibraryWriteRefused(Exception):
    """A write into a library folder will not be carried out; the message is for the person."""


@dataclass(frozen=True, slots=True)
class Staged:
    """A name claimed in a library folder, and a scratch path beside it to build the file at.

    Same folder, so finishing is an atomic rename; no media extension, so the scan skips it."""

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
    """Forget a folder and its subtree; grants go first, as a leftover could name a reused id."""
    folder = await library.get_folder(folder_id)
    if folder is None:
        # Already gone by cascade from an ancestor.
        return False
    for inside in await library.folders_under(folder):
        await access.forget_object(ObjectType.FOLDER, inside.id)
    await access.forget_object(ObjectType.FOLDER, folder.id)
    return await library.remove_folder(folder.id)


async def require_root(library: LibraryStore, root_id: str) -> Root:
    """The root a file belongs to, refused as a sentence when it has left the library."""
    root = await library.get_root(root_id)
    if root is None:
        raise LibraryWriteRefused("The folder this file was in is no longer part of your library.")
    return root


async def check_folder_may_change(directory: Path) -> None:
    """Refuse in a sentence unless Sift may write into this folder (never its parent) now."""
    try:
        await asyncio.to_thread(check_folder_writable, directory)
    except LibraryError as refusal:
        raise LibraryWriteRefused(str(refusal)) from refusal


async def check_may_change(directory: Path) -> None:
    """The ordinary way in, for a caller with one folder to write into."""
    await check_folder_may_change(directory)


async def create_directory(directory: Path, name: str) -> Path:
    """Make one folder inside a library, or refuse in a sentence somebody can act on."""
    await check_folder_may_change(directory)
    try:
        wanted = check_folder_name(name)
    except InvalidFilename as refusal:
        raise LibraryWriteRefused(str(refusal)) from refusal

    made = directory / wanted
    # `mkdir` alone is race-safe; this check only gives a better sentence than the system's.
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
    """Rename or move a folder inside a library, refusing a move into itself."""
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
        # Not `os.replace`: on Windows it replaces a file with a directory without complaint.
        await asyncio.to_thread(source.rename, destination)
    except OSError as failure:
        raise LibraryWriteRefused(
            f"Sift could not move that folder ({failure.strerror})."
        ) from failure
