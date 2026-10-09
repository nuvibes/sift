# SPDX-License-Identifier: AGPL-3.0-or-later
"""Adding, changing and removing a library root, and remembering a refusal.
Removing drops the grants first: a grant left after the delete names an id that can be reused."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path

from sift.kernel.access import ObjectType, Repository, Viewer
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce_now, telling
from sift.kernel.content import ROOT_REL_PATH, FolderRow, LibraryStore, Root, RootKind
from sift.kernel.content.mounts import is_remote
from sift.kernel.db import Database, IntegrityError, Params
from sift.kernel.filenames import InvalidFilename, check_folder_name
from sift.kernel.ids import new_id
from sift.kernel.ingress import IngressRejected
from sift.kernel.ledger import Actor
from sift.kernel.library_write import (
    LibraryWriteRefused,
    check_folder_may_change,
    create_directory,
    forget_folder,
    move_directory,
    require_root,
)
from sift.kernel.log import get_logger
from sift.kernel.paths import confine
from sift.kernel.wiring import Part

log = get_logger(__name__)


def _inside(base: Path, rel_path: str) -> Path:
    """Where a folder really is, proved inside its library. Blocking: `confine` follows links."""
    return confine(base, base / rel_path) if rel_path else confine(base, base)


def _folder_on_disk(base: Path, rel_path: str) -> Path | None:
    """The directory at `rel_path`, or None where nothing is there; a non-directory is refused.
    Blocking."""
    at = base / rel_path
    if not at.exists() and not at.is_symlink():
        return None
    if not at.is_dir():
        raise LibraryWriteRefused(
            f"There's already something called \"{at.name}\" in that folder, and it isn't a folder."
        )
    return _inside(base, rel_path)


async def _put_back(source: Path, destination: Path) -> str:
    """A move the rows refused, undone on the disk so the two still agree, and said either way."""
    try:
        await move_directory(destination, source)
    except LibraryWriteRefused:
        return "Sift moved the folder but couldn't record where it went. Rescan this library."
    return (
        "Sift couldn't record that move, so the folder is back where it was. Rescan this library."
    )


# One row per path; a changed file overwrites its own row.
_REMEMBER_REJECTION = """
INSERT INTO scan_rejections
  (id, root_id, rel_path, size_bytes, mtime_ns, reason, detected, first_seen_at, last_seen_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(root_id, rel_path) DO UPDATE SET
  size_bytes    = excluded.size_bytes,
  mtime_ns      = excluded.mtime_ns,
  reason        = excluded.reason,
  detected      = excluded.detected,
  last_seen_at  = excluded.last_seen_at
"""

_REJECTION_AT = """
SELECT size_bytes, mtime_ns FROM scan_rejections WHERE root_id = ? AND rel_path = ?
"""

# The refusal is about the bytes at the path, so it goes when they do.
_FORGET_REJECTION = "DELETE FROM scan_rejections WHERE root_id = ? AND rel_path = ?"

#: Not a gate reason: a person said no.
REMOVED_FROM_SIFT = "removed_from_sift"

_REMOVED_INSIDE = """
SELECT rel_path, size_bytes FROM scan_rejections
 WHERE root_id = ? AND reason = ? AND substr(rel_path, 1, ?) = ?
"""

# Read once per pass: refusals are few, files are many.
_REJECTIONS_OF_ROOT = "SELECT rel_path, size_bytes, mtime_ns FROM scan_rejections WHERE root_id = ?"

# A moved folder takes its refusals with it, or every refused file is gated again.
_MOVE_REJECTIONS = """
UPDATE scan_rejections
   SET rel_path = :new || substr(rel_path, :cut)
 WHERE root_id = :root
   AND (rel_path = :old OR substr(rel_path, 1, :prefix_length) = :prefix)
"""


class LibraryService:
    """What the library screens and the scanner do to roots. One per application."""

    def __init__(
        self,
        database: Database,
        library: LibraryStore,
        access: Repository,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._db = database
        self._library = library
        self._access = access
        self._clock = clock

    def _now(self) -> int:
        return int(self._clock())

    async def add_root(
        self,
        *,
        abs_path: Path,
    ) -> Root:
        """Point Sift at a directory, or refuse with a `LibraryError` written for the person
        choosing it."""
        root = await self._library.create_root(
            name=abs_path.name or str(abs_path),
            abs_path=abs_path,
            kind=RootKind.NAS if is_remote(abs_path) else RootKind.LOCAL,
        )
        # Moves every admin's library list without touching a permission, so it is announced here.
        announce_now(EVERY_ADMIN, About.LIBRARY)
        return root

    async def repoint_root(self, root_id: str, abs_path: Path) -> Root | None:
        """Tell Sift where a library folder is now. None when there is no such root."""
        root = await self._library.repoint_root(root_id, abs_path)
        if root is not None:
            announce_now(EVERY_ADMIN, About.LIBRARY)
        return root

    async def set_root_hidden(self, viewer: Viewer, root_id: str, *, hidden: bool) -> None:
        """Hide a library, or bring it back, for this user only."""
        await self._library.set_root_hidden(viewer.id, root_id, hidden=hidden)

    async def remove_root(self, root_id: str, *, actor: Actor) -> bool:
        """Forget a root, its folders, and every permission that named them, grants first. Touches
        no file."""
        root = await self._library.get_root(root_id)
        if root is None:
            return False

        for folder_id in await self._library.folder_ids_in_root(root_id):
            await self._access.forget_object(ObjectType.FOLDER, folder_id)
        await self._access.forget_object(ObjectType.ROOT, root_id)

        return await self._library.delete_root(root_id, actor=actor)

    async def create_folder(self, *, parent_id: str, name: str) -> FolderRow:
        """Create a folder inside a library, on the disk first, then in the tree."""
        parent = await self._library.get_folder(parent_id)
        if parent is None:
            raise LibraryWriteRefused("That folder isn't there any more.")
        root = await require_root(self._library, parent.root_id)

        directory = await asyncio.to_thread(_inside, Path(root.abs_path), parent.rel_path)
        made = await create_directory(directory, name)

        rel_path = f"{parent.rel_path}/{made.name}" if parent.rel_path else made.name
        folder = await self._library.upsert_folder(root.id, rel_path)
        log.info("library.folder_created", root_id=root.id, folder_id=folder.id)
        # See `add_root`.
        announce_now(EVERY_ADMIN, About.LIBRARY)
        return folder

    async def place_folder(self, *, parent_id: str, name: str) -> FolderRow:
        """The folder called `name` inside another: recorded if already on the disk, created if not."""
        parent = await self._library.get_folder(parent_id)
        if parent is None:
            raise LibraryWriteRefused("That folder isn't there any more.")
        root = await require_root(self._library, parent.root_id)
        try:
            wanted = check_folder_name(name)
        except InvalidFilename as refusal:
            raise LibraryWriteRefused(str(refusal)) from refusal
        rel_path = f"{parent.rel_path}/{wanted}" if parent.rel_path else wanted
        there = await asyncio.to_thread(_folder_on_disk, Path(root.abs_path), rel_path)
        if there is None:
            return await self.create_folder(parent_id=parent_id, name=wanted)
        await check_folder_may_change(there)
        folder = await self._library.upsert_folder(root.id, rel_path)
        log.info("library.folder_recorded", root_id=root.id, folder_id=folder.id)
        announce_now(EVERY_ADMIN, About.LIBRARY)
        return folder

    async def move_folder(
        self, *, folder_id: str, parent_id: str | None, name: str | None, actor: Actor
    ) -> FolderRow:
        """Rename a folder, move it, or both: one act on the disk and one rewrite of the rows."""
        folder = await self._library.get_folder(folder_id)
        if folder is None:
            raise LibraryWriteRefused("That folder isn't there any more.")
        if folder.rel_path == ROOT_REL_PATH:
            raise LibraryWriteRefused(
                "This is the library folder itself. To move it, tell Sift where it is now."
            )
        root = await require_root(self._library, folder.root_id)

        holder = folder.rel_path.rsplit("/", 1)[0] if "/" in folder.rel_path else ROOT_REL_PATH
        if parent_id is not None:
            wanted = await self._library.get_folder(parent_id)
            if wanted is None or wanted.root_id != folder.root_id:
                raise LibraryWriteRefused(
                    "A folder can only be moved somewhere inside the same library folder."
                )
            holder = wanted.rel_path

        # This feature's own refusal, so the route answers with the sentence rather than a 500.
        try:
            called = check_folder_name(name) if name is not None else folder.name
        except InvalidFilename as refusal:
            raise LibraryWriteRefused(str(refusal)) from refusal
        rel_path = f"{holder}/{called}" if holder else called
        if rel_path == folder.rel_path:
            return folder

        base = Path(root.abs_path)
        source = await asyncio.to_thread(_inside, base, folder.rel_path)
        destination = await asyncio.to_thread(_inside, base, rel_path)
        await move_directory(source, destination)

        try:
            moved = await self._library.move_folder(folder, rel_path, actor=actor)
        except IntegrityError:
            moved = None
        if moved is None:
            raise LibraryWriteRefused(await _put_back(source, destination))
        await self.move_rejections(
            root_id=root.id, old_rel_path=folder.rel_path, new_rel_path=rel_path
        )
        log.info("library.folder_rearranged", root_id=root.id, folder_id=moved.id)
        # Announced here, not left to `move_rejections`. See `add_root`.
        announce_now(EVERY_ADMIN, About.LIBRARY)
        return moved

    async def remove_folder(self, *, root_id: str, rel_path: str) -> bool:
        """Forget one folder that a walk saw is gone, by path, and every permission that named it."""
        folder = await self._library.folder_at(root_id, rel_path)
        if folder is None:
            # Already gone by cascade.
            return False
        return await forget_folder(self._library, self._access, folder.id)

    async def _say(self, sql: str, params: Params) -> None:
        """Write, and tell every admin's screens on commit, only when a row actually moved."""
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            await connection.execute(sql, params)

    async def remember_rejection(
        self,
        *,
        root_id: str,
        rel_path: str,
        size_bytes: int,
        mtime_ns: int,
        rejection: IngressRejected,
    ) -> None:
        """Write down that this file was refused, so the next scan does not say so again."""
        now = self._now()
        await self._say(
            _REMEMBER_REJECTION,
            (
                new_id(),
                root_id,
                rel_path,
                size_bytes,
                mtime_ns,
                rejection.reason.value,
                rejection.detected,
                now,
                now,
            ),
        )
        log.info(
            "library.file_refused",
            root_id=root_id,
            reason=rejection.reason.value,
            detected=rejection.detected,
        )

    async def remember_removed(self, *, root_id: str, rel_path: str, size_bytes: int) -> None:
        """Remove a picture inside an archive from Sift, as a Skipped refusal keyed by its own size."""
        now = self._now()
        await self._say(
            _REMEMBER_REJECTION,
            (new_id(), root_id, rel_path, size_bytes, 0, REMOVED_FROM_SIFT, None, now, now),
        )
        log.info("library.member_removed", root_id=root_id)

    async def removed_inside(self, *, root_id: str, archive_rel_path: str) -> dict[str, int]:
        """The pictures of one archive somebody removed from Sift: each path, with its size."""
        prefix = archive_rel_path + "/"
        rows = await self._db.fetch_all(
            _REMOVED_INSIDE, (root_id, REMOVED_FROM_SIFT, len(prefix), prefix)
        )
        return {str(row["rel_path"]): int(row["size_bytes"]) for row in rows}

    async def was_already_refused(
        self, *, root_id: str, rel_path: str, size_bytes: int, mtime_ns: int
    ) -> bool:
        """Whether this exact file was refused before and has not changed size or mtime since."""
        row = await self._db.fetch_one(_REJECTION_AT, (root_id, rel_path))
        if row is None:
            return False
        return int(row["size_bytes"]) == size_bytes and int(row["mtime_ns"]) == mtime_ns

    async def rejections_of_root(self, root_id: str) -> dict[str, tuple[int, int]]:
        """Every refusal under a root, by path, with the size and mtime the refused bytes had."""
        rows = await self._db.fetch_all(_REJECTIONS_OF_ROOT, (root_id,))
        return {
            str(row["rel_path"]): (int(row["size_bytes"]), int(row["mtime_ns"])) for row in rows
        }

    async def forget_rejection(self, *, root_id: str, rel_path: str) -> None:
        """The file at this path is readable now, or is gone. Either way the refusal is stale."""
        await self._say(_FORGET_REJECTION, (root_id, rel_path))

    async def adopt_locations(self, folder: FolderRow) -> None:
        """Give a folder row the present files directly in it that have no folder."""
        await self._library.adopt_locations(folder)

    async def move_rejections(self, *, root_id: str, old_rel_path: str, new_rel_path: str) -> None:
        """A folder moved, so what it was refusing moved with it. See `_MOVE_REJECTIONS`."""
        prefix = old_rel_path + "/"
        await self._say(
            _MOVE_REJECTIONS,
            {
                "root": root_id,
                "old": old_rel_path,
                "prefix": prefix,
                "prefix_length": len(prefix),
                "cut": len(old_rel_path) + 1,
                "new": new_rel_path,
            },
        )

    async def rejection_count(self) -> int:
        """How many files the scanner is walking past, across every folder, in one `COUNT`."""
        (row,) = await self._db.fetch_all("SELECT COUNT(*) AS total FROM scan_rejections", ())
        return int(row["total"])


SERVICE: Part[LibraryService] = Part("library_service")
