# SPDX-License-Identifier: AGPL-3.0-or-later
"""The only code in Sift that renames or moves something in a library.

Sift indexes files where they already are and writes nothing into a library, with two exceptions
that are features rather than accidents: deleting a file, and this: renaming one, and moving one
to another folder.

    rename(asset_id, new_name=..., actor=admin)          the same file, a different name
    move(asset_id, folder_id=..., actor=admin)           the same file, a different folder

Every one of them is refused unless the filesystem lets Sift write in the folder, and refused
again for anyone who is not an admin. Both refusals happen here, before anything is
touched: a read-only folder is turned down with a sentence somebody can act on, never by the
filesystem raising in the middle of the operation. By then the question has been answered wrongly
on screen, and half the work may already be done.

Two things about renaming deserve saying plainly, because they are why this is written the way it
is rather than as a one-line call to the operating system.

**A rename destroys as easily as a delete.** `os.replace` overwrites whatever is at the destination
without a word, and `os.rename` will too. Renaming a file onto a name that is taken is a silent
deletion of somebody else's file, carried out by a button labelled "rename". So the name is claimed
first, exclusively, and the claim fails if anything is already there, rather than the destination
being checked and then written to, which answers the question and then acts on the answer a moment
later, when it may no longer be true.

**Identity is the content, not the path.** The digest is what says which file this is, so moving one
changes an address and nothing else: the same asset row, with its tags, its rating and its people
still attached. That is not a happy accident of the implementation: the location row keeps its id
and only its path columns change, and the index is updated beside the disk rather than by a later
scan noticing.
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

#: What a recorded move was. Both are the same operation to a filesystem and different things to
#: the person who asked for one, and the history reads as nonsense if it cannot tell them apart.
MoveKind = Literal["rename", "move"]

#: Re-exported rather than restated. The rule about what a filename may be is the kernel's, because
#: the editor accepts a typed name too and two copies of it would let one screen take a name the
#: other refuses.
MAX_FILENAME_LENGTH = filenames.MAX_FILENAME_LENGTH


class OrganizeRefused(LibraryWriteRefused):
    """A rename or move will not be carried out, and the message says why.

    Written for the person who pressed the button rather than for whoever debugs it. The router
    passes them through unchanged.

    It extends the kernel's refusal so that a feature which PRODUCES a file (and reaches this
    service through the write seam rather than by importing it) can catch a refusal without
    importing this module. Nothing else changes: every existing raise and except still means what
    it meant.
    """


class NotFound(OrganizeRefused):
    """There is nothing here to organize, as far as the user asking is concerned.

    The same answer for "no such asset" and "an asset you may not see", deliberately. Telling the
    two apart would let anyone confirm a file exists by asking to rename it.
    """


class NotAllowed(OrganizeRefused):
    """The user may see the file but may not do this to it."""


class VaultLocked(NotFound, ConcealedByVault):
    """The one refusal a person is owed the truth about: their OWN vault is concealing it.

    A `NotFound` still, so everything that already catches one keeps working; the marker is what
    lifts the answer from "there is no such file" to "it is in your vault, and here is the way in".
    See `sift.kernel.reach.ConcealedByVault`, which carries the whole argument for why saying this
    to that one user gives nothing away.
    """


@dataclass(frozen=True, slots=True)
class Organized:
    """What a rename or a move turned out to be, as the screen needs it.

    The new name, and the id of the record that can undo it. No path: where a file sits on the
    server's disk is a fact about the machine and does not belong in a response, for the same
    reason a root's absolute path does not.
    """

    asset_id: str
    location_id: str
    filename: str
    folder_id: str | None
    move_id: str


@dataclass(frozen=True, slots=True)
class Place:
    """Where one file of a batch sits: its location, the folder on disk, and whether that folder
    is on another machine. No path leaves the server: this is for the batch's own planning."""

    location: Location
    directory: Path
    remote: bool


@dataclass(frozen=True, slots=True)
class Organizability:
    """Whether this asset's files can be renamed or moved, and if not, why not.

    The interface asks before it draws the menu, because these actions are hidden on a folder that
    was not handed over read-write rather than shown greyed out. An action that is visible and
    refuses is an invitation to a dead end; one that is absent says the same thing without asking
    anybody to try.
    """

    can_organize: bool
    reason: str | None = None


def check_filename(name: str) -> str:
    """A file's new name, and nothing else. Never a path.

    The rule itself is the kernel's, and what this adds is the refusal type. A route here answers a
    bad name with the status it has always answered with, and the sentence comes from the one place
    that decides what a safe name is, so the rename box and the editor's name box cannot come to
    disagree about which names are allowed.
    """
    try:
        return filenames.check_filename(name)
    except filenames.InvalidFilename as refused:
        raise OrganizeRefused(str(refused)) from refused


def claim_and_move(source: Path, destination: Path) -> None:
    """Move a file to a name nothing else holds, or refuse. Never overwrites.

    The name is taken before the file is put there, and it is taken with a create that fails if
    anything already exists at that path. That ordering is the point. Checking whether the
    destination exists and then renaming onto it is two operations with a gap in between, and
    something arriving in that gap is destroyed silently, which is exactly the failure that makes
    a rename as dangerous as a delete.

    Once the empty placeholder is ours, replacing it is safe: the only thing being overwritten is
    the file this function created a moment ago. If the move then fails the placeholder is cleared
    up, so a failed rename does not leave a zero-length file wearing the name somebody wanted.

    Blocking, and called from a thread. Both ends are inside one root, hence on one filesystem, so
    this is a rename rather than a copy: instant, atomic, and it needs no free space.
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
        # Ours to remove: nothing but this function has ever had this path, and it has held it
        # since the create above. Cleared with the failure rather than left behind, so a rename
        # that did not happen does not leave an empty file holding the name.
        with contextlib.suppress(OSError):
            os.unlink(destination)
        raise OrganizeRefused(
            f"Sift couldn't move that file ({failure.strerror}). Check that it isn't open in "
            "another program and that Sift is allowed to write to this folder."
        ) from failure


#: What a scratch file being built in a library folder is called.
#:
#: Two properties, both load-bearing. It does not end in a media extension, so the folder walk,
#: which filters on the extension before it stats anything, goes straight past it: a half-written
#: video carrying a real extension is one the scan tries to take in while it is still being written.
#: And it carries an id, so two operations producing the same output name at the same time build in
#: different files rather than into each other.
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
    """The write seam. One per application, reached at `app.state.organizer`.

    Everything that renames or moves something in a library goes through here, and a rule in the
    build refuses any code outside this package that renames or moves a file at all. So this class
    is the whole of the blast radius, and it is meant to stay small enough to read in one sitting.
    """

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

    # --- the seam ---------------------------------------------------------------------------

    async def rename(
        self,
        asset_id: str,
        *,
        new_name: str,
        actor: Viewer,
        location_id: str | None = None,
    ) -> Organized:
        """Give a file a different name in the folder it is already in.

        The folder does not change, so this is a move whose destination folder is the source
        folder, and it is written as one, rather than as a second implementation that would have
        to be kept agreeing with the first about confinement, collisions and the index.
        """
        location, root = await self._target(asset_id, location_id, actor)
        filename = check_filename(new_name)
        # A name typed without an extension keeps the one the file has. The rename box opens on
        # the whole name, so a name typed over it has none; stored that way, the file lost the
        # ending its type is read from, and the next folder walk, which goes by the ending,
        # passed it by. A batch rename keeps it for the same reason (`batch.py`). An ending that
        # was typed is taken as typed.
        kept = Path(location.filename).suffix
        if kept and not Path(filename).suffix:
            filename += kept
        if filename == location.filename:
            # Otherwise this reaches the claim and is refused as a collision, with itself,
            # reported as "there is already something called that", which is true and useless.
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
        # No folder named at all is a different thing from a folder that is not there, and they get
        # different answers: this one is an incomplete request, and saying "there is no such folder"
        # about a folder nobody named would be a confusing way to report it.
        if not folder_id:
            raise OrganizeRefused("Say which folder to move the file to.")
        folder = await self._library.get_folder(folder_id)
        if folder is None:
            raise NotFound("There's no such folder.")

        # Into another library folder, when the two are on the same disk.
        #
        # Two roots CAN be two different disks, and then a move is a copy of every byte followed by
        # a delete, which is a different operation with different failure modes and has to be built
        # as one. Two roots on the same disk are the ordinary case (several folders under one
        # drive) and there a move is an instant rename.
        #
        # So the question asked is the one the operation itself will ask: are these on the same
        # filesystem, not "are these the same library folder". The target root is looked up
        # exactly as the source is, and the folder the file arrives in is asked about below,
        # because that is where the write lands.
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
        """Write, and tell the screens that list what changed.

        Every write here that moves something a screen draws goes through this rather than straight
        to the database: a new one is added by writing a statement, which is the moment when nothing
        reminds anybody that a screen somewhere is showing the old answer.

        Every admin, because these are admin-only decisions about somebody's library and the screens
        that draw them refuse a guest. Announced after the write rather than on its commit, since
        these are single statements outside any transaction of their own.
        """
        await self._db.execute(sql, params)
        announce_now(EVERY_ADMIN, About.LIBRARY)

    async def undo(self, move_id: str, *, actor: Viewer) -> Organized:
        """Put a file back at the address it had before a rename or a move.

        The reverse of the operation it names, run through exactly the same path as the operation
        itself, so undoing is refused for the same reasons doing it would be, and cannot overwrite
        something that has since taken the old name. An undo that forced its way back would be a
        silent deletion performed by the button whose whole job is to prevent one.
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

        # The file has to still be where this move left it, or this is not the move being taken
        # back. Undoing an older move out of order would send the file to whatever name that record
        # happens to hold: for a file renamed twice, a name it only ever had in passing, with the
        # name it started with then unreachable because the newer record was marked undone too.
        # An undo either reverses the last thing that happened to this file, or it is refused.
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
        # Marked only once the file is really back. Marking it first would leave an entry claiming
        # to have been undone by an operation that then refused, and no way to try again.
        await self._say(_MARK_UNDONE, (self.now(), move_id))
        log.info("organize.undone", move_id=move_id, asset_id=undone.asset_id)
        return undone

    async def naming_facts(self, asset_ids: Sequence[str]) -> dict[str, dict[str, object]]:
        """What a naming template can read about each file, by id: the kernel's own read."""
        return await self._content.naming_facts(asset_ids)

    async def places(self, asset_ids: Sequence[str], *, actor: Viewer) -> dict[str, Place | str]:
        """Where each of these files sits, or the sentence saying why it cannot be renamed.

        For a batch, which plans every name before it writes any. The reads are asked once for the
        whole batch (who may see which file, where each one is, each root and each folder once)
        rather than once a file, so a preview of a thousand files is a handful of reads. The
        refusals are the ones `_target` makes, in the same order, and each rename still asks them
        again for its own file when it is carried out: this plans, it grants nothing.
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
            if not locations:
                answers[asset_id] = OUT_OF_REACH
                continue
            if len(locations) > 1:
                answers[asset_id] = (
                    "This file is in more than one folder, so a batch leaves it alone. Rename it "
                    "from its own page."
                )
                continue
            location = locations[0]
            if location.inside_an_archive:
                answers[asset_id] = "This picture is inside an archive, so it keeps its name."
                continue
            if location.status is not LocationStatus.PRESENT:
                answers[asset_id] = "Sift can't find that file where it expects it to be."
                continue
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
        """Whether this user can rename or move this asset's file, and why not if it cannot.

        Answered before the menu is drawn, so the actions can be absent on a read-only folder
        rather than present and refusing. It asks the same questions in the same order as the
        operations do, so what the interface shows and what the server would do cannot disagree.
        """
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
    # Compressing or editing a file produces a NEW file, and it lands next to the one it came from.
    # That is a write into somebody's library, so it happens here, under the same refusals as a
    # rename, and through the same claim-then-move, which means it can no more land on top of
    # something than a rename can.
    #
    # It is three calls because the bytes are written by ffmpeg, over minutes, into a path this
    # decided on. Everything that can be settled before a single byte is written is settled in the
    # first call; the third exists so a failure does not leave a scratch file in somebody's folder.

    async def writable_beside(self, asset_id: str, *, actor: Viewer) -> str | None:
        """Whether a produced file could be written beside this one. The reason it could not, or None.

        The same question `organizability` answers, asked by the same code, so what a panel offers
        and what the server would do cannot disagree, and so that "compress is not offered on a
        folder handed over read-only" is the same rule as "move is not offered" rather than a second
        one that has to be kept in step with it.
        """
        answer = await self.organizability(asset_id, actor=actor)
        return None if answer.can_organize else (answer.reason or "Sift cannot write here.")

    async def name_taken_beside(self, asset_id: str, *, filename: str, actor: Viewer) -> bool:
        """Whether something is already called this in the folder beside that file.

        The same question `stage_beside` asks, asked by the same code and early enough to be shown
        to somebody. A route that only QUEUES work answers 200, the screen says the file is being
        saved, and without this the job would then die out of sight because the name was taken,
        with nothing on screen ever saying so.

        It produces nothing and stages nothing, so it is safe to ask while somebody is still
        dragging. It is also not a guarantee: something can arrive in that folder a moment later,
        and `keep` is where that is really settled, atomically.
        """
        destination = await self._beside(asset_id, filename, actor)
        return await asyncio.to_thread(destination.exists)

    async def _beside(self, asset_id: str, filename: str, actor: Viewer) -> Path:
        """Where a produced file called `filename` would land beside `asset_id`.

        Pulled out of `stage_beside` rather than written twice: the two must agree about which path
        they are talking about, or the check above would be answering about somewhere else.
        """
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

        Asks exactly what a rename asks, in the same order and of the same functions: can this
        user see the file, is it an admin, does the filesystem allow the write, and does the name
        stay inside the root. A produced file arriving in a folder is as much a
        write as a renamed one leaving it.

        The name being free is checked here as well, and that check is a courtesy rather than the
        guarantee: something can still arrive in the folder while ffmpeg runs. `keep` is where it
        is really settled, atomically. Asking now means a person is told before four minutes of
        encoding rather than after.
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
        """Give the finished file its real name, or refuse. Never overwrites.

        The same claim-then-move every rename goes through, unmodified and for the same reason: the
        destination name is taken with a create that fails if anything is there, so there is no
        moment in which something else's file could be replaced. Both paths are in one directory, so
        this is a rename: instant, and it cannot half-happen.

        Nothing is recorded in the move history. A produced file was not moved from anywhere; the
        record that it exists is the asset the scan makes of it, and the record of what it came from
        belongs to the feature that produced it.
        """
        root = await self._writable_root(staged.root_id)
        root_path = Path(root.abs_path)
        destination = await self._confined(root_path, root_path / staged.rel_path)
        await self._check_writable(destination.parent)
        # A produced file keeps no place: ffmpeg copies a MOV's or an MKV's location tag into a
        # trim of it. `working` is the scratch file this operation built, never a library file.
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
        """Clear up a scratch file whose work did not finish.

        Only ever the scratch path, which nothing but this operation has ever held, so there is
        no question of removing something somebody else put there. Silent when there is nothing to
        remove: a failure before ffmpeg wrote anything is the ordinary case.
        """
        await asyncio.to_thread(_unlink_quietly, staged.working)

    # --- the operation ----------------------------------------------------------------------

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

        Both ends are confined to the root before anything happens, and confinement here means the
        real, symlink-followed path: a `..` is not the only way out of a folder, and a symlink
        inside one pointing outside it is a perfectly ordinary relative path that lands the write
        somewhere the permissions were never resolved against.

        The disk moves first and the index follows immediately. That order is the survivable one.
        An index updated first and a move that then failed would leave every screen pointing at a
        path the file does not have: the file is fine and Sift cannot find it, which looks exactly
        like data loss to the person it happens to. The other way round, a move that succeeded and a
        database write that did not leaves a file the next scan finds: same bytes, same digest, so
        it reconnects to the same asset with everything recorded about it still attached.
        """
        # Before anything moves: the path has to be one that can actually be stored. The name check
        # and the column's own check are two different pieces of code, and if they ever disagree
        # this is where it has to surface: a file that moves and a row that then refuses to be
        # written is the one outcome this module exists to prevent.
        try:
            to_rel_path = check_rel_path(to_rel_path)
        except ValueError as refusal:
            raise OrganizeRefused("That isn't a name Sift can store.") from refusal

        root_path = Path(root.abs_path)
        source = await self._path_of(location)
        destination = await self._confined(root_path, root_path / to_rel_path)

        # Both ends. The file leaves one directory and arrives in another, and on a move they are
        # different directories that can differ in whether Sift may write to them.
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
        """Write the history line that says a file moved, and that can take the move back.

        `mover` is the user who asked, or None for a move Sift made as its own act. `moved_by` is a
        key into the `users` table, so there is no id to write for Sift; None is the honest value,
        and the history reads it as nobody named.
        """
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
                # Nobody asked: the move is Sift's own, and its line says Sift.
                1 if mover is None else 0,
                reason,
                self.now(),
            ),
        )
        return move_id

    # --- the refusals -----------------------------------------------------------------------

    async def _unreachable(self, actor: Viewer, asset_id: str) -> OrganizeRefused:
        """Why this file could not be reached, as the refusal to raise.

        Only on the refusal path, so nothing that succeeds pays for the second read: the same
        arrangement as `kernel.reach.refuse_one`, which a service cannot use because that one
        builds an HTTP exception.

        The undifferentiated answer stays the default: a file somebody was never shown and a file
        that is not there wear one face, deliberately. The asker's own vault is the exception, and
        it is the case this exists for: "There is no such file" is no answer to somebody looking
        at the padlock of the file they were trying to move.
        """
        if await conceals(self._access, actor, asset_id):
            return VaultLocked(VAULT_LOCKED)
        return NotFound(OUT_OF_REACH)

    async def _target(
        self, asset_id: str, location_id: str | None, actor: Viewer
    ) -> tuple[Location, Root]:
        """The file this operation is about, having proved it may be touched at all.

        Visibility is settled before permission, so somebody who cannot see a file is told it does
        not exist rather than that they are not allowed to rename it. The second answer confirms
        it is there.

        The one exception is the asker's OWN vault, which is told the truth. See `_unreachable`.
        """
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
            # The same bytes in two folders. Which one is being renamed is not something to guess
            # at, and renaming both would be a second operation nobody asked for.
            raise OrganizeRefused(
                "This file is in more than one folder. Say which copy to rename or move."
            )

        location = locations[0]
        return location, await self._writable_root(location.root_id)

    async def _writable_root(self, root_id: str) -> Root:
        """The root this file sits in, refused as a sentence when it has left the library.

        The lookup is the kernel's (`kernel.library_write.require_root`) and this only turns
        its refusal into an organize's. What is left to prove about a write is asked of the
        filesystem, per folder, by `_check_writable`.
        """
        try:
            return await require_root(self._library, root_id)
        except LibraryWriteRefused as refusal:
            raise OrganizeRefused(str(refusal)) from refusal

    async def _check_writable(self, directory: Path) -> None:
        """That the filesystem allows Sift to write in this folder.

        Asked per folder rather than once, because a move writes into two of them and either can be
        the one that refuses.
        """
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


def _parent_rel_path(rel_path: str) -> str:
    """The folder a path sits in, as a path relative to the root.

    A file at the top of a root has no folder above it within the root, which is the empty path:
    the same value the root's own folder row carries.
    """
    head, separator, _ = rel_path.rpartition("/")
    return head if separator else ""


#: The one thing in Sift that renames or moves a file somebody else put there.
ORGANIZER: Part[Organizer] = Part("organizer")
