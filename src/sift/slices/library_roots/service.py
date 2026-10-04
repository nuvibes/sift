# SPDX-License-Identifier: AGPL-3.0-or-later
"""Adding, changing and removing a library root, and remembering a refusal.

Thin on purpose. The rules that decide whether a directory may be a root (that roots do not
overlap, that none of them is a directory Sift writes to) live in the kernel beside the table
they protect, so that no caller can reach the table without passing them. What is here is the work
that sits either side of that: dropping the permissions that name a root before it goes, and the
scanner's memory of what it refused.

The one thing worth reading is `remove_root`. Deleting a root cascades its folders away, and
nothing cascades the grants that name those folders: `acl_grants.object_id` carries no foreign key
because the id beside it could belong to any of half a dozen tables. So they are dropped
deliberately, and they are dropped *first*. Forget-then-delete leaves grants naming a root that is
already gone, which is inert; delete-then-forget leaves grants naming ids that a later root or
folder can be given, which is a share nobody granted.
"""

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
from sift.kernel.db import Database, Params, Row
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
    """Where a folder really is, proved to be inside its library before it is handed to anything.

    Blocking (`confine` resolves and follows links), so callers put it on a thread. A folder row
    can name an in-library link that leads out of the root, and this is what stops one being made,
    renamed or moved through.
    """
    return confine(base, base / rel_path) if rel_path else confine(base, base)


def _folder_on_disk(base: Path, rel_path: str) -> Path | None:
    """The directory at `rel_path` inside its library, or None where nothing is there. Blocking.

    Something there that is not a directory is refused in a sentence: it is a name already taken,
    and recording it as a folder would give downloads a place they can never land.
    """
    at = base / rel_path
    if not at.exists() and not at.is_symlink():
        return None
    if not at.is_dir():
        raise LibraryWriteRefused(
            f"There's already something called \"{at.name}\" in that folder, and it isn't a folder."
        )
    return _inside(base, rel_path)


# One row per path. A file that changed since it was refused overwrites its own row rather than
# adding a second one, and `excluded` is how the upsert reads the values it was handed.
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

# A file that came back, or was replaced by one Sift can read. The refusal is not a fact about the
# path, it is a fact about the bytes that were at it, so it goes when they do.
_FORGET_REJECTION = "DELETE FROM scan_rejections WHERE root_id = ? AND rel_path = ?"

#: The reason a picture inside an archive is left out after somebody removed it from Sift. Not one
#: of the gate's reasons: nothing was wrong with the bytes; a person said no to them.
REMOVED_FROM_SIFT = "removed_from_sift"

# The pictures of one archive somebody removed, by the archive's own path plus a slash.
_REMOVED_INSIDE = """
SELECT rel_path, size_bytes FROM scan_rejections
 WHERE root_id = ? AND reason = ? AND substr(rel_path, 1, ?) = ?
"""

# Every refusal under one root at once, for a pass that is about to ask about every file in it.
# Asked per file instead, it would be one indexed read for each of a hundred thousand files, almost
# all answering "no row". Refusals are few: a root's worth is a small mapping, read once.
_REJECTIONS_OF_ROOT = "SELECT rel_path, size_bytes, mtime_ns FROM scan_rejections WHERE root_id = ?"

# A folder that moved takes its refusals with it. The refusal is about the BYTES that were at a
# path (see `forget_rejection`), and those bytes did not change because the folder they sit in
# was renamed. Left behind, every refused file in the folder is put through the gate again and
# logged again on the next pass, which is the exact noise the memory exists to stop.
#
# Written here rather than beside the four columns the kernel rewrites, because this table belongs
# to this feature and the kernel does not write a feature's tables. It is the same act a line
# later; the cost of it not being in the same transaction is a stale refusal, which is one extra
# read of one file.
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

    # --- roots -----------------------------------------------------------------------------

    async def add_root(
        self,
        *,
        abs_path: Path,
    ) -> Root:
        """Point Sift at a directory, or refuse in a sentence somebody can act on.

        Neither the name nor the kind is asked for. The name is what the directory is called on
        disk, and the kind is read off the filesystem it is mounted from: both are facts about
        the machine, and asking somebody to restate a fact is asking them to get it wrong.

        Every refusal this can raise is a `LibraryError`, whose message is written to be read by
        the person choosing the folder rather than by whoever has to debug it. The router turns one
        into a 400 and hands the message over unchanged.
        """
        root = await self._library.create_root(
            name=abs_path.name or str(abs_path),
            abs_path=abs_path,
            kind=RootKind.NAS if is_remote(abs_path) else RootKind.LOCAL,
        )
        # Somebody else's library list just gained a row, and nothing else would tell them.
        #
        # This and the two folder verbs below are the shape the change bus's second way in exists
        # for: a write that moves a list without moving anybody's permission. No user's view was
        # resolved, so there is no audience to announce to except the reach of every admin, settled
        # at the moment of sending. The writes that REMOVE one need none of this: dropping the
        # grants goes through `forget_object`, which announces on its own commit.
        announce_now(EVERY_ADMIN, About.LIBRARY)
        return root

    async def repoint_root(self, root_id: str, abs_path: Path) -> Root | None:
        """Tell Sift where a library folder is now. None when there is no such root.

        Refused in a `LibraryError` for anything adding it would refuse (see
        `LibraryStore.repoint_root`, which does the work). Here for the same reason `add_root` is:
        every admin's library list shows where a folder is, it has just moved, and nothing else
        would tell them. No user's view was resolved, so the audience is every admin.
        """
        root = await self._library.repoint_root(root_id, abs_path)
        if root is not None:
            announce_now(EVERY_ADMIN, About.LIBRARY)
        return root

    async def set_root_hidden(self, viewer: Viewer, root_id: str, *, hidden: bool) -> None:
        """Hide a library, or bring it back, for this user only.

        A pass-through to the store, kept here so the route talks to one object. Whether the caller
        may do it is settled at the route.
        """
        await self._library.set_root_hidden(viewer.id, root_id, hidden=hidden)

    async def remove_root(self, root_id: str, *, actor: Actor) -> bool:
        """Forget a root, its folders, and every permission that named them. Touches no file.

        The files' records stay, with their tags, people and ratings, so adding the folder back
        brings everything with it, for thirty days. After that the Maintenance card that removes
        stranded records may throw them away, and the screen says so where the folder is removed.

        The grants go first. A grant naming a root that has already gone is inert (nothing will
        ever look it up), but a grant left behind after the delete names an id, and an id is not
        a promise never to be reused. Fail closed: if dropping the grants raises, the root is
        still there, still enforcing them, and the next attempt can be made.
        """
        root = await self._library.get_root(root_id)
        if root is None:
            return False

        for folder_id in await self._library.folder_ids_in_root(root_id):
            await self._access.forget_object(ObjectType.FOLDER, folder_id)
        await self._access.forget_object(ObjectType.ROOT, root_id)

        return await self._library.delete_root(root_id, actor=actor)

    async def create_folder(self, *, parent_id: str, name: str) -> FolderRow:
        """Make a folder inside a library, on the disk and in the tree, in that order.

        The disk first, deliberately. A row written before the directory exists describes a folder
        nobody can put anything in, and the failure arrives later, somewhere else, as a download
        that cannot land. Made first, a refusal is a sentence in front of the person who asked.

        The row is written straight away rather than being left for the next scan. A folder somebody
        made in order to download into it is no use to them at the next walk of their library.
        """
        parent = await self._library.get_folder(parent_id)
        if parent is None:
            raise LibraryWriteRefused("That folder isn't there any more.")
        root = await require_root(self._library, parent.root_id)

        directory = await asyncio.to_thread(_inside, Path(root.abs_path), parent.rel_path)
        made = await create_directory(directory, name)

        rel_path = f"{parent.rel_path}/{made.name}" if parent.rel_path else made.name
        folder = await self._library.upsert_folder(root.id, rel_path)
        log.info("library.folder_created", root_id=root.id, folder_id=folder.id)
        # A folder list somebody else is looking at now has one more folder in it. See `add_root`.
        announce_now(EVERY_ADMIN, About.LIBRARY)
        return folder

    async def place_folder(self, *, parent_id: str, name: str) -> FolderRow:
        """The folder called `name` inside another: recorded where it is on the disk already, and
        made where it is not.

        For a place somebody chose to download into, where "there" is the question and "new" is
        not. A folder on the disk that no walk has recorded yet (an empty one a scan has not
        reached) is still their folder, and making one is refused there, so it is recorded
        instead, under the same permission a write needs: downloads are about to land in it.
        `create_folder` keeps its own refusal of a name already taken, for the press that means
        "make a new one".
        """
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
        """Rename a folder, move it, or both. One act on the disk, one rewrite of the rows.

        Renaming and moving are the same operation to a filesystem and are the same one here, so
        there is one path through the checks rather than two that have to agree. Either half may be
        left out: a rename says a name, a move says a parent, and doing both at once is what
        dragging a folder somewhere and typing over its name is.

        **Sift doing this itself is what makes it free of guesswork.** A folder rearranged in a file
        manager has to be RECOGNISED on the next walk, from what is inside it; a folder rearranged
        here is simply told to move, and the rows and the disk change together.
        """
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

        # Re-raised as this feature's own refusal, which is what the route catches. Left as the
        # kernel's, a name with a slash in it would come back as a 500 rather than as the sentence
        # written for the person who typed it, and the dialog they typed it in would have nothing
        # to draw.
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

        moved = await self._library.move_folder(folder, rel_path, actor=actor)
        if moved is None:
            raise LibraryWriteRefused(
                "Sift moved the folder but couldn't record where it went. Rescan this library."
            )
        await self.move_rejections(
            root_id=root.id, old_rel_path=folder.rel_path, new_rel_path=rel_path
        )
        log.info("library.folder_rearranged", root_id=root.id, folder_id=moved.id)
        # The folder is under a different name, or somewhere else, on every screen drawing it.
        # See `add_root`.
        #
        # Said here even though `move_rejections` just above announces on its own account: that is
        # a bookkeeping write which will one day skip the folders that refused nothing, and this
        # rule would go silently with it. A move's liveness is not the rejection sweep's to own.
        announce_now(EVERY_ADMIN, About.LIBRARY)
        return moved

    async def remove_folder(self, *, root_id: str, rel_path: str) -> bool:
        """Forget one folder of a library, and every permission that named it. Touches no file.

        Only ever called where a walk positively saw that the directory is not there. A folder that
        could not be READ is not a folder that has gone, and the caller is what tells the two
        apart. That is the only thing this method adds: it names the folder by its PATH, because a
        walk has a path and not an id.

        The rule about what a removal has to take with it (and, more to the point, the order the
        two stores have to be written in) is `forget_folder` in the kernel, because the delete
        feature needs it too and a slice may not import another slice: kept here, it would be a
        second copy of the one thing about this that can be got wrong.
        """
        folder = await self._library.folder_at(root_id, rel_path)
        if folder is None:
            # Already gone: an ancestor was removed first and took it by cascade. Not a failure:
            # the outcome asked for is the outcome there is.
            return False
        return await forget_folder(self._library, self._access, folder.id)

    # --- what the scanner refused ----------------------------------------------------------

    async def _say(self, sql: str, params: Params) -> None:
        """Write, and tell the screens that list what changed.

        Every write here that moves something a screen draws goes through this rather than straight
        to the database: a new one is added by writing a statement, which is the moment when nothing
        reminds anybody that a screen somewhere is showing the old answer.

        Every admin, because these are admin-only decisions about somebody's library and the screens
        that draw them refuse a guest.

        The application's one write-and-tell wrapper rather than a second one of this slice's own,
        and the difference is not cosmetic: it announces on the COMMIT and only when a row actually
        moved. Most of these writes are conditional (a file refused again on the next walk, a
        rejection cleared that was already gone), and saying so on the attempt would have every
        admin's screen re-reading a page for a change that did not happen.
        """
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
        """Write down that this file was refused, so the next scan does not say so again.

        The reason is stored, and the file's name is not: `rel_path` is the key and the reason
        describes the decision rather than the file. What is never stored is anything the gate read
        out of the bytes.
        """
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
        """A picture inside an archive was removed from Sift, so the next scan leaves it out.

        Written as one of the scanner's refusals, so it is listed under Skipped with Try again
        beside it, which is the way back. Remembered against the picture's own size and not an
        age: a picture carries its archive's mtime, so any change to the archive would otherwise
        bring back every picture somebody removed from it. No `detected`: nothing was read.
        """
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
        """Whether this exact file has been refused before and has not changed since.

        Not "whether this path was refused": a path whose file has been replaced is a different
        file, and it gets the gate again. Size and mtime together are what says so (see the
        module docstring for why this is not a digest).
        """
        row = await self._db.fetch_one(_REJECTION_AT, (root_id, rel_path))
        if row is None:
            return False
        return int(row["size_bytes"]) == size_bytes and int(row["mtime_ns"]) == mtime_ns

    async def rejections_of_root(self, root_id: str) -> dict[str, tuple[int, int]]:
        """Every refusal under a root, by path: the size and mtime the refused bytes had.

        What a pass reads once and then consults per file, in place of a read per file. The same
        rule as `was_already_refused`, applied to the mapping: a path is refused while the file at
        it still has the size and age the refusal recorded.
        """
        rows = await self._db.fetch_all(_REJECTIONS_OF_ROOT, (root_id,))
        return {
            str(row["rel_path"]): (int(row["size_bytes"]), int(row["mtime_ns"])) for row in rows
        }

    async def forget_rejection(self, *, root_id: str, rel_path: str) -> None:
        """The file at this path is readable now, or is gone. Either way the refusal is stale."""
        await self._say(_FORGET_REJECTION, (root_id, rel_path))

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

    async def rejections_in_root(self, root_id: str, *, limit: int) -> list[Row]:
        """What a root is currently refusing, for the screen that says so. The first `limit` by
        path; `rejection_count_in_root` says how many there are in all."""
        return await self._db.fetch_all(
            "SELECT * FROM scan_rejections WHERE root_id = ? ORDER BY rel_path LIMIT ?",
            (root_id, limit),
        )

    async def rejection_count_in_root(self, root_id: str) -> int:
        row = await self._db.fetch_one(
            "SELECT COUNT(*) AS n FROM scan_rejections WHERE root_id = ?", (root_id,)
        )
        return 0 if row is None else int(row["n"])

    async def rejection_count(self) -> int:
        """How many files the scanner is walking past, across every folder at once.

        One statement rather than a listing per folder folded together, because the card on the
        board wants a number and asking each root for its rows to measure how many there are is a
        read per folder for something a `COUNT` answers.
        """
        (row,) = await self._db.fetch_all("SELECT COUNT(*) AS total FROM scan_rejections", ())
        return int(row["total"])


#: Adding and removing a root, and what the scanner refused.
SERVICE: Part[LibraryService] = Part("library_service")
