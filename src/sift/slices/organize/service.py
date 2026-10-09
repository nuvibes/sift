# SPDX-License-Identifier: AGPL-3.0-or-later
"""The only code in Sift that renames or moves something in a library.

Both are admin-only and refused before anything is touched where Sift may not write. A rename claims
the new name exclusively first, since `os.replace` would silently overwrite; a move keeps the asset
row and changes only its location's path.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sift.kernel import filenames, places
from sift.kernel.access import Repository, Viewer
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce_now
from sift.kernel.content import (
    ContentStore,
    LibraryStore,
    Location,
    LocationStatus,
    Root,
    check_rel_path,
    subtree_prefix,
)
from sift.kernel.content.mounts import is_remote
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.library_write import (
    LibraryWriteRefused,
    Placed,
    Staged,
    check_folder_may_change,
    require_root,
)
from sift.kernel.log import get_logger
from sift.kernel.paths import PathEscape, confine, same_filesystem
from sift.kernel.reach import OUT_OF_REACH, VAULT_LOCKED, ConcealedByVault, conceals
from sift.kernel.wiring import Part

log = get_logger(__name__)

#: What a recorded move was: the same operation to a filesystem, two different things to a person.
MoveKind = Literal["rename", "move"]

#: Re-exported: what a filename may be is the kernel's rule, shared with the editor.
MAX_FILENAME_LENGTH = filenames.MAX_FILENAME_LENGTH


class OrganizeRefused(LibraryWriteRefused):
    """A rename or move will not be carried out, and the message says why, for whoever pressed it.

    It extends the kernel's refusal, so a feature reaching this through the write seam can catch it.
    """


class NotFound(OrganizeRefused):
    """There is nothing here to organize: no such asset and an unseen one answer alike, deliberately."""


class NotAllowed(OrganizeRefused):
    """The user may see the file but may not do this to it."""


class VaultLocked(NotFound, ConcealedByVault):
    """The one refusal a person is owed the truth about: their own vault is concealing it."""


@dataclass(frozen=True, slots=True)
class Organized:
    """What a rename or a move turned out to be: the new name and the undo record's id, never a path."""

    asset_id: str
    location_id: str
    filename: str
    folder_id: str | None
    move_id: str


@dataclass(frozen=True, slots=True)
class Place:
    """Where one file of a batch sits, for the batch's own planning; no path leaves the server."""

    location: Location
    directory: Path
    remote: bool


@dataclass(frozen=True, slots=True)
class Organizability:
    """Whether this asset's files can be renamed or moved, and if not, why not, asked before the menu."""

    can_organize: bool
    reason: str | None = None


def check_filename(name: str) -> str:
    """A file's new name, and nothing else; the rule is the kernel's."""
    try:
        return filenames.check_filename(name)
    except filenames.InvalidFilename as refused:
        raise OrganizeRefused(str(refused)) from refused


def claim_and_move(source: Path, destination: Path) -> None:
    """Move a file to a name nothing else holds, or refuse. Never overwrites.

    The name is claimed with an exclusive create first, so nothing arriving meanwhile is destroyed;
    a failed move clears the placeholder. Blocking, called from a thread; one filesystem, so a
    rename.
    """
    try:
        handle = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exists:
        raise OrganizeRefused(
            f"There's already something called '{destination.name}' in that folder. "
            "Pick a different name."
        ) from exists
    except OSError as failure:
        raise OrganizeRefused(
            f"Sift couldn't put the file there ({failure.strerror}). Check that Sift is allowed "
            "to write to this folder."
        ) from failure
    os.close(handle)

    try:
        os.replace(source, destination)
    except OSError as failure:
        # Ours to remove: only this function ever held this path.
        with contextlib.suppress(OSError):
            os.unlink(destination)
        raise OrganizeRefused(
            f"Sift couldn't move that file ({failure.strerror}). Check that it isn't open in "
            "another program and that Sift is allowed to write to this folder."
        ) from failure


#: What a scratch file in a library folder is called: no media extension, so the walk skips it, and
#: an id, so two operations never share one.
WORKING_SUFFIX = ".sift-part"


def working_name(filename: str, token: str) -> str:
    """The scratch name a produced file is built under, before it is given its real one."""
    return f".{filename}.{token}{WORKING_SUFFIX}"


_INSERT_MOVE = """
INSERT INTO file_moves
  (id, kind, location_id, asset_id, root_id, from_rel_path, from_folder_id, to_rel_path,
   to_folder_id, moved_by, moved_by_sift, reason, moved_at, undone_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
"""

_MOVE_ROW = "SELECT * FROM file_moves WHERE id = ?"

_MARK_UNDONE = "UPDATE file_moves SET undone_at = ? WHERE id = ? AND undone_at IS NULL"

_LATEST_FOR_ASSET = """
SELECT * FROM file_moves
 WHERE asset_id = ? AND undone_at IS NULL
 ORDER BY moved_at DESC, id DESC
 LIMIT 1
"""


class Organizer:
    """The write seam, reached at `app.state.organizer`; nothing else in Sift renames or moves a file."""

    def __init__(
        self,
        database: Database,
        content: ContentStore,
        library: LibraryStore,
        access: Repository,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._db = database
        self._content = content
        self._library = library
        self._access = access
        self._clock = clock

    def now(self) -> int:
        return int(self._clock())

    async def rename(
        self,
        asset_id: str,
        *,
        new_name: str,
        actor: Viewer,
        location_id: str | None = None,
    ) -> Organized:
        """Give a file a different name in the folder it is already in: a move within its own folder."""
        location, root = await self._target(asset_id, location_id, actor)
        filename = check_filename(new_name)
        # A name typed without an extension keeps the file's own, which the folder walk reads.
        kept = Path(location.filename).suffix
        if kept and not Path(filename).suffix:
            filename += kept
        if filename == location.filename:
            # Otherwise this would collide with itself.
            raise OrganizeRefused("The file is already called that.")
        folder = subtree_prefix(_parent_rel_path(location.rel_path))
        return await self._relocate(
            location,
            root,
            to_rel_path=folder + filename,
            to_folder_id=location.folder_id,
            kind="rename",
            mover=actor.id,
        )

    async def move(
        self,
        asset_id: str,
        *,
        folder_id: str,
        actor: Viewer,
        location_id: str | None = None,
    ) -> Organized:
        """Put a file in a different folder, under the name it already has."""
        location, location_root = await self._target(asset_id, location_id, actor)
        root = location_root
        # No folder named is an incomplete request, not a missing folder.
        if not folder_id:
            raise OrganizeRefused("Say which folder to move the file to.")
        folder = await self._library.get_folder(folder_id)
        if folder is None:
            raise NotFound("There's no such folder.")

        # Into another library folder: allowed where the two are on the same filesystem, where a
        # move is a rename.
        if folder.root_id != location.root_id:
            root = await self._writable_root(folder.root_id)
            if not await asyncio.to_thread(
                same_filesystem, Path(location_root.abs_path), Path(root.abs_path)
            ):
                raise OrganizeRefused(
                    "That folder is on a different disk, and Sift can't move files between disks "
                    "yet. Move the file into a folder on the same disk, or copy it across yourself "
                    "and Sift will find it."
                )

        to_rel_path = subtree_prefix(folder.rel_path) + location.filename
        if root.id == location.root_id and to_rel_path == location.rel_path:
            raise OrganizeRefused("The file is already in that folder.")

        return await self._relocate(
            location,
            root,
            to_rel_path=to_rel_path,
            to_folder_id=folder.id,
            kind="move",
            mover=actor.id,
        )

    async def _say(self, sql: str, params: tuple[object, ...]) -> None:
        """Write, and tell every admin's screens that list what changed, after the write."""
        await self._db.execute(sql, params)
        announce_now(EVERY_ADMIN, About.LIBRARY)

    async def undo(self, move_id: str, *, actor: Viewer) -> Organized:
        """Put a file back where it was before a rename or a move, through the same path, so it cannot
        overwrite anything.
        """
        if not actor.is_admin:
            raise NotAllowed("Only an admin can undo a move.")

        row = await self._db.fetch_one(_MOVE_ROW, (move_id,))
        if row is None:
            raise NotFound("There's nothing here to undo.")
        if row["undone_at"] is not None:
            raise OrganizeRefused("That move has already been undone.")

        location = await self._content.location(str(row["location_id"]))
        if location is None:
            raise OrganizeRefused(
                "That file is no longer in your library, so there's nothing to put back."
            )

        # The file must still be where this move left it: only the last move of a file is undone.
        if str(row["to_rel_path"]) != location.rel_path or str(row["root_id"]) != location.root_id:
            raise OrganizeRefused(
                "That file has moved since, so this move can't be undone by itself. Undo the most "
                "recent move first."
            )

        root = await self._writable_root(location.root_id)
        undone = await self._relocate(
            location,
            root,
            to_rel_path=str(row["from_rel_path"]),
            to_folder_id=row["from_folder_id"],
            kind=str(row["kind"]),  # type: ignore[arg-type]
            mover=actor.id,
            record=False,
        )
        # Marked only once the file is really back.
        await self._say(_MARK_UNDONE, (self.now(), move_id))
        log.info("organize.undone", move_id=move_id, asset_id=undone.asset_id)
        return undone

    async def naming_facts(self, asset_ids: Sequence[str]) -> dict[str, dict[str, object]]:
        """What a naming template can read about each file, by id: the kernel's own read."""
        return await self._content.naming_facts(asset_ids)

    async def places(self, asset_ids: Sequence[str], *, actor: Viewer) -> dict[str, Place | str]:
        """Where each of these files sits, or the sentence saying why it cannot be renamed.

        Planned for a batch in a handful of reads; each rename still asks again when it is carried
        out.
        """
        wanted = list(dict.fromkeys(asset_ids))
        if not actor.is_admin:
            return dict.fromkeys(wanted, "Only an admin can rename or move files.")
        visible = await self._access.visible_of(actor, wanted)
        located = await self._content.locations_of([one for one in wanted if one in visible])
        roots: dict[str, Root | str] = {}
        folders: dict[tuple[str, str], tuple[Path, bool] | str] = {}
        answers: dict[str, Place | str] = {}
        for asset_id in wanted:
            locations = located.get(asset_id, []) if asset_id in visible else []
            refusal = _not_for_a_batch(locations)
            if refusal is not None:
                answers[asset_id] = refusal
                continue
            location = locations[0]
            if location.root_id not in roots:
                try:
                    roots[location.root_id] = await self._writable_root(location.root_id)
                except OrganizeRefused as refused:
                    roots[location.root_id] = str(refused)
            root = roots[location.root_id]
            if isinstance(root, str):
                answers[asset_id] = root
                continue
            parent = _parent_rel_path(location.rel_path)
            key = (root.id, parent)
            if key not in folders:
                folders[key] = await self._folder_of(Path(root.abs_path), parent)
            folder = folders[key]
            if isinstance(folder, str):
                answers[asset_id] = folder
                continue
            answers[asset_id] = Place(location=location, directory=folder[0], remote=folder[1])
        return answers

    async def _folder_of(self, root_path: Path, parent: str) -> tuple[Path, bool] | str:
        """One folder of a batch on disk, whether Sift may write in it, and whether it is remote."""
        try:
            directory = await self._confined(root_path, root_path / parent)
            await self._check_writable(directory)
        except OrganizeRefused as refused:
            return str(refused)
        return directory, await asyncio.to_thread(is_remote, directory)

    async def last_move(self, asset_id: str) -> str | None:
        """The id of the most recent move of this asset that has not been taken back."""
        row = await self._db.fetch_one(_LATEST_FOR_ASSET, (asset_id,))
        return None if row is None else str(row["id"])

    async def organizability(self, asset_id: str, *, actor: Viewer) -> Organizability:
        """Whether this user can rename or move this asset's file, asking what the operations ask."""
        if await self._access.open_asset(actor, asset_id) is None:
            raise await self._unreachable(actor, asset_id)
        if not actor.is_admin:
            return Organizability(False, "Only an admin can rename or move files.")
        try:
            location, _root = await self._target(asset_id, None, actor)
            await self._check_writable((await self._path_of(location)).parent)
        except OrganizeRefused as refusal:
            return Organizability(False, str(refusal))
        return Organizability(True)

    # --- producing a new file beside an existing one ------------------------------------------
    #
    # A produced file is a write into a library, so it goes through the same refusals and the same
    # claim-then-move as a rename, in three calls around the encode.

    async def writable_beside(self, asset_id: str, *, actor: Viewer) -> str | None:
        """Whether a produced file could be written beside this one; the reason it could not, or None."""
        answer = await self.organizability(asset_id, actor=actor)
        return None if answer.can_organize else (answer.reason or "Sift cannot write here.")

    async def name_taken_beside(self, asset_id: str, *, filename: str, actor: Viewer) -> bool:
        """Whether something is already called this beside that file: shown early, settled by `keep`."""
        destination = await self._beside(asset_id, filename, actor)
        return await asyncio.to_thread(destination.exists)

    async def _beside(self, asset_id: str, filename: str, actor: Viewer) -> Path:
        """Where a produced file called `filename` would land beside `asset_id`."""
        location, root = await self._target(asset_id, None, actor)
        name = check_filename(filename)
        root_path = Path(root.abs_path)
        folder = subtree_prefix(_parent_rel_path(location.rel_path))
        try:
            rel_path = check_rel_path(folder + name)
        except ValueError as refusal:
            raise OrganizeRefused("That isn't a name Sift can store.") from refusal
        return await self._confined(root_path, root_path / rel_path)

    async def stage_beside(self, asset_id: str, *, filename: str, actor: Viewer) -> Staged:
        """Settle whether a produced file may be written beside this one, and say where to build it.

        The same checks as a rename, in the same order; the free name is checked now as a courtesy.
        """
        location, root = await self._target(asset_id, None, actor)
        name = check_filename(filename)
        root_path = Path(root.abs_path)
        destination = await self._beside(asset_id, filename, actor)
        rel_path = str(destination.relative_to(root_path).as_posix())
        await self._check_writable(destination.parent)

        if await asyncio.to_thread(destination.exists):
            raise OrganizeRefused(
                f"There's already something called '{name}' in that folder. Sift won't write "
                "over it."
            )

        working = await self._confined(root_path, destination.parent / working_name(name, new_id()))
        return Staged(
            asset_id=location.asset_id,
            location_id=location.id,
            root_id=root.id,
            folder_id=location.folder_id,
            rel_path=rel_path,
            filename=name,
            working=working,
        )

    async def keep(self, staged: Staged) -> Placed:
        """Give the finished file its real name, or refuse. Never overwrites; nothing is recorded as a
        move.
        """
        root = await self._writable_root(staged.root_id)
        root_path = Path(root.abs_path)
        destination = await self._confined(root_path, root_path / staged.rel_path)
        await self._check_writable(destination.parent)
        # A produced file keeps no place tag; `working` is the scratch file, never a library file.
        await asyncio.to_thread(places.remove_places_from_own, staged.working)
        await asyncio.to_thread(claim_and_move, staged.working, destination)
        log.info("organize.produced", asset_id=staged.asset_id, root_id=staged.root_id)
        return Placed(
            root_id=staged.root_id,
            folder_id=staged.folder_id,
            rel_path=staged.rel_path,
            filename=staged.filename,
            path=destination,
        )

    async def discard(self, staged: Staged) -> None:
        """Clear up a scratch file whose work did not finish; silent when there is nothing there."""
        await asyncio.to_thread(_unlink_quietly, staged.working)

    async def _relocate(
        self,
        location: Location,
        root: Root,
        *,
        to_rel_path: str,
        to_folder_id: str | None,
        kind: MoveKind,
        mover: str | None,
        record: bool = True,
        reason: str | None = None,
    ) -> Organized:
        """Move one file and repoint its index entry. The single path every operation takes.

        Both ends are confined by their real, symlink-followed paths. The disk moves first: a failed
        index write after it leaves a file the next scan reconnects.
        """
        # Before anything moves: the path must be one the column will store.
        try:
            to_rel_path = check_rel_path(to_rel_path)
        except ValueError as refusal:
            raise OrganizeRefused("That isn't a name Sift can store.") from refusal

        root_path = Path(root.abs_path)
        source = await self._path_of(location)
        destination = await self._confined(root_path, root_path / to_rel_path)

        # Both ends, which can differ in whether Sift may write.
        await self._check_writable(source.parent)
        await self._check_writable(destination.parent)

        await asyncio.to_thread(claim_and_move, source, destination)

        moved = await self._content.relocate(
            location.id, root_id=root.id, rel_path=to_rel_path, folder_id=to_folder_id
        )
        if moved is None:  # pragma: no cover - the row was read moments ago in the same request
            raise OrganizeRefused("That file is no longer in your library.")

        move_id = ""
        if record:
            move_id = await self._record(location, moved, kind=kind, mover=mover, reason=reason)

        log.info(
            "organize.moved",
            asset_id=moved.asset_id,
            location_id=moved.id,
            kind=kind,
            recorded=record,
        )
        return Organized(
            asset_id=moved.asset_id,
            location_id=moved.id,
            filename=moved.filename,
            folder_id=moved.folder_id,
            move_id=move_id,
        )

    async def _record(
        self,
        before: Location,
        after: Location,
        *,
        kind: MoveKind,
        mover: str | None,
        reason: str | None = None,
    ) -> str:
        """Write the history line for a move, which can take it back; `mover` None is Sift's own move."""
        move_id = new_id()
        await self._say(
            _INSERT_MOVE,
            (
                move_id,
                kind,
                after.id,
                after.asset_id,
                after.root_id,
                before.rel_path,
                before.folder_id,
                after.rel_path,
                after.folder_id,
                mover,
                1 if mover is None else 0,
                reason,
                self.now(),
            ),
        )
        return move_id

    async def _unreachable(self, actor: Viewer, asset_id: str) -> OrganizeRefused:
        """Why this file could not be reached, asked only on refusal; the asker's own vault is told so."""
        if await conceals(self._access, actor, asset_id):
            return VaultLocked(VAULT_LOCKED)
        return NotFound(OUT_OF_REACH)

    async def _target(
        self, asset_id: str, location_id: str | None, actor: Viewer
    ) -> tuple[Location, Root]:
        """The file this operation is about, visibility settled before permission. See `_unreachable`."""
        if await self._access.open_asset(actor, asset_id) is None:
            raise await self._unreachable(actor, asset_id)
        if not actor.is_admin:
            raise NotAllowed("Only an admin can rename or move files.")

        locations = await self._content.locations(asset_id)
        if location_id is not None:
            locations = [each for each in locations if each.id == location_id]
        if not locations:
            raise NotFound(OUT_OF_REACH)
        if len(locations) > 1:
            # The same bytes in two folders: which one is meant is not to be guessed.
            raise OrganizeRefused(
                "This file is in more than one folder. Say which copy to rename or move."
            )

        location = locations[0]
        return location, await self._writable_root(location.root_id)

    async def _writable_root(self, root_id: str) -> Root:
        """The root this file sits in, refused as a sentence when it has left the library."""
        try:
            return await require_root(self._library, root_id)
        except LibraryWriteRefused as refusal:
            raise OrganizeRefused(str(refusal)) from refusal

    async def _check_writable(self, directory: Path) -> None:
        """That the filesystem allows Sift to write in this folder, asked per folder."""
        try:
            await check_folder_may_change(directory)
        except LibraryWriteRefused as refusal:
            raise OrganizeRefused(str(refusal)) from refusal

    async def _confined(self, root: Path, candidate: Path) -> Path:
        try:
            return await asyncio.to_thread(confine, root, candidate)
        except PathEscape as escape:
            raise OrganizeRefused("That isn't somewhere Sift can put this file.") from escape

    async def _path_of(self, location: Location) -> Path:
        try:
            return await self._content.path_of(location)
        except (LookupError, ValueError) as failure:
            raise OrganizeRefused(
                "Sift can't find that file where it expects it to be."
            ) from failure


def _unlink_quietly(path: Path) -> None:
    """Remove a scratch file if it is there. Blocking, and called from a thread."""
    with contextlib.suppress(OSError):
        os.unlink(path)


def _not_for_a_batch(locations: Sequence[Location]) -> str | None:
    """Why a batch leaves a file with these locations alone, or None when it may rename it."""
    if not locations:
        return OUT_OF_REACH
    if len(locations) > 1:
        return (
            "This file is in more than one folder, so a batch leaves it alone. Rename it "
            "from its own page."
        )
    location = locations[0]
    if location.inside_an_archive:
        return "This picture is inside an archive, so it keeps its name."
    if location.status is not LocationStatus.PRESENT:
        return "Sift can't find that file where it expects it to be."
    return None


def _parent_rel_path(rel_path: str) -> str:
    """The folder a path sits in, relative to the root; empty at the top of the root."""
    head, separator, _ = rel_path.rpartition("/")
    return head if separator else ""


#: The one thing in Sift that renames or moves a file somebody else put there.
ORGANIZER: Part[Organizer] = Part("organizer")
