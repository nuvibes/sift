# SPDX-License-Identifier: AGPL-3.0-or-later
"""The only code in Sift that removes a file somebody else put there.

Everything else in the application indexes in place and writes nothing into a library. This one
module is the exception, and it is written so the dangerous thing is hard and the safe thing is
easy.

Two operations share the entry point and they are not variations of each other:

    remove(asset_id, mode="sift", actor=viewer)   forget the index entry; the file is untouched
    remove(asset_id, mode="disk", actor=viewer)   unlink the file; there is no way back

Both are admin-only. The first is the safe one on disk: a re-scan finds the file again and the
digest gives it back the identity it had, but it is still a change to the shared library: it drops
every location the asset has, copies in folders the caller cannot see included, and once the last
one goes it cascades away the tags, people, collections and ratings attached to it. That is
curation, which belongs to an admin, so forgetting is an admin's too. The second removes real bytes
from a real disk, and is refused again for any folder that was not handed over read-write.

**There is no bin.** A deleted file is deleted. What stands between somebody and a mistake is the
confirmation in front of the button, and nothing here, so the checks below are the whole of the
safety, and the wording of every refusal matters more than it would if a mistake could be taken
back.

**The refusals happen here, before anything is touched.** Every location's folder is checked
first, and only then is the first file removed, so a delete over four files does not remove three
and fail on the fourth. A read-only folder is turned down by this code with a sentence somebody can
act on, never by the filesystem raising at the moment of the unlink: by then the question has
already been answered wrongly on screen.

The order of the checks is the other thing worth reading. Whether the asking user can see the
file at all is settled before whether it is allowed to delete it, so somebody who cannot see a file
is told it does not exist rather than that they are not allowed to delete it. The second answer
would confirm it is there.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, cast

from sift.kernel.access import ObjectType, Repository, Viewer
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.content import (
    ContentStore,
    Derivative,
    FolderRow,
    LibraryStore,
    Location,
)
from sift.kernel.db import Database
from sift.kernel.ledger import MOST_SUBJECTS, Actor, Object, record_event
from sift.kernel.library_write import (
    LibraryWriteRefused,
    check_may_change,
    forget_folder,
    require_root,
)
from sift.kernel.log import get_logger
from sift.kernel.paths import confine
from sift.kernel.reach import (
    OUT_OF_REACH,
    OUT_OF_REACH_MANY,
    VAULT_LOCKED,
    VAULT_LOCKED_MANY,
    ConcealedByVault,
    conceals,
)
from sift.kernel.seams import PlaybackCacheSeam, RemovedMembersSeam
from sift.kernel.vocabulary import Subject, SubjectKind
from sift.kernel.wiring import Part

log = get_logger(__name__)

#: How a delete is meant to be carried out. The safe one is written first and is the default
#: everywhere a default is offered.
Mode = Literal["sift", "disk"]

#: HOW MANY OF THE THINGS A FILE WAS ON ONE DELETE EVENT NAMES, beside the file itself.
#: The door's ceiling for a whole event, less the one the file takes. Read from the door rather
#: than written out again: a file on nine people would otherwise be refused outright, and the
#: refusal would arrive at the moment somebody pressed Delete.
MOST_ENTITIES: Final = MOST_SUBJECTS - 1

#: EVERYTHING A FILE WAS ON, read while the rows are still there.
#: The event has to name them because nothing else can afterwards: every one of these link rows
#: cascades away with the file, so a person's page asking "what happened to the files of theirs"
#: has only what the event wrote down. The names come back with the ids for the same reason the
#: file's name does: the event outlives what it names.
#:
#: `UNION` and not `UNION ALL`: a file on two usernames of one Site names that Site once, and a
#: duplicate would eat one of the seven places without saying anything new.
#:
#: The rank column is what makes the order the file's own (people, then tags, then the Sites it
#: is filed under, then the shelves it sits on, which is the order a file's record lists them)
#: and it is what decides which seven are kept when there are more. A compound select has no order
#: of its own, so without it the seven would be whatever the planner happened to produce.
_WAS_ON = """
WITH gone(id) AS (SELECT value FROM json_each(?))
SELECT link.asset_id AS asset_id, 0 AS rank, 'person' AS kind, p.id AS id, p.name AS name
  FROM asset_people link
  JOIN gone ON gone.id = link.asset_id
  JOIN people p ON p.id = link.person_id
 UNION
SELECT link.asset_id, 1, 'tag', t.id, t.name
  FROM asset_tags link
  JOIN gone ON gone.id = link.asset_id
  JOIN tags t ON t.id = link.tag_id
 UNION
SELECT link.asset_id, 2, 'site', s.id, s.name
  FROM asset_usernames link
  JOIN gone ON gone.id = link.asset_id
  JOIN usernames ac ON ac.id = link.username_id
  JOIN sites s ON s.id = ac.site_id
 UNION
SELECT link.asset_id, 3, 'collection', c.id, c.name
  FROM collection_items link
  JOIN gone ON gone.id = link.asset_id
  JOIN collections c ON c.id = link.collection_id
 ORDER BY asset_id, rank, name
"""


#: The song each of these files carries, read while the link is still there, for the reason the
#: rest of what a file was on is (`_WAS_ON`). A file carries one song at most.
_SONG_OF = """
WITH gone(id) AS (SELECT value FROM json_each(?))
SELECT link.asset_id AS asset_id, s.id AS id, s.name AS name
  FROM song_files link
  JOIN gone ON gone.id = link.asset_id
  JOIN songs s ON s.id = link.song_id
"""

#: Which list of a delete's payload each kind of thing a file was on goes in.
_LISTED: Final = {"person": "people", "tag": "tags", "site": "sites", "collection": "collections"}


@dataclass(frozen=True, slots=True)
class WasOn:
    """Everything a file was, read before it goes: what it was on (`_WAS_ON`, in the file's own
    order), and the facts Insights adds up once there is no file to ask (`facts`, the payload's
    words for them)."""

    on: dict[str, list[Subject]]
    facts: dict[str, dict[str, object]]


#: WHY A PICTURE INSIDE AN ARCHIVE IS NOT DELETED FROM DISK, and what works instead. Its bytes are
#: in the archive, and Sift does not rewrite a person's archive; unlinking the copy pulled out into
#: the cache would report a delete while the next scan brought the picture straight back.
INSIDE_AN_ARCHIVE: Final = (
    "That picture is inside a ZIP file, and Sift doesn't change ZIP files, so it can't be deleted "
    "from disk. Remove it from Sift instead: it stays out of Sift, and the ZIP file stays as it is."
)
INSIDE_AN_ARCHIVE_MANY: Final = (
    "Those pictures are inside a ZIP file, and Sift doesn't change ZIP files, so they can't be "
    "deleted from disk. Remove them from Sift instead: they stay out of Sift, and the ZIP file "
    "stays as it is."
)


def _inside_archives(
    asset_ids: Sequence[str], places: Mapping[str, Sequence[Location]]
) -> list[str]:
    """The files of these that sit inside an archive, in the order given."""
    return [
        asset_id
        for asset_id in asset_ids
        if any(one.inside_an_archive for one in places.get(asset_id, []))
    ]


def _said_of_many(refusal: DeleteRefused) -> str:
    """A refusal said of a selection. Most name no count, so one wording is as true of thirty
    files as of one; an archive's names the picture, so it carries its own plural."""
    return refusal.many if isinstance(refusal, InsideAnArchive) else str(refusal)


class DeleteRefused(Exception):
    """A delete will not be carried out, and the message says why.

    Every message here is written to be read by the person who pressed the button rather than by
    whoever has to debug it. The router hands them over unchanged.
    """


class NotFound(DeleteRefused):
    """There is nothing here to delete, as far as the user asking is concerned.

    Deliberately the same answer for "no such asset" and "an asset you may not see". Telling the
    two apart would let anyone confirm a file exists by asking to delete it.
    """


class NotAllowed(DeleteRefused):
    """The user may see the file but may not do this to it."""


class InsideAnArchive(DeleteRefused):
    """A disk delete of a picture that lives inside an archive: refused, with what works instead."""

    #: The same refusal said of a selection, which only the bulk route has.
    many = INSIDE_AN_ARCHIVE_MANY


class VaultLocked(NotFound, ConcealedByVault):
    """The one refusal a person is owed the truth about: their OWN vault is concealing it.

    A `NotFound` still, so everything that already catches one keeps working; the marker is what
    lifts the answer from "there is no such file" to "it is in your vault, and here is the way in".
    See `sift.kernel.reach.ConcealedByVault`, which carries the whole argument for why saying this
    to that one user gives nothing away.
    """


def _delete_file(target: Path) -> None:
    """Remove a file, treating one that is already gone as success.

    A file somebody removed from underneath Sift is not a reason to refuse the rest of a delete,
    and raising on it would leave a half-done operation whose remaining half can never be retried:
    the index entry would still be there pointing at nothing.
    """
    with contextlib.suppress(FileNotFoundError):
        os.unlink(target)


def _remove_directory(target: Path) -> bool:
    """Remove one EMPTY directory. Says whether it went, and never raises.

    `rmdir` and not a recursive remove, and that is the whole safety property of the folder delete:
    it can only ever take away a directory whose contents have already been removed one file at a
    time, each one an indexed file that Sift was given write access to. A tree remove would delete
    whatever else happened to be in there (artwork, subtitles, a folder of somebody's own), with
    no bin and no record, on the strength of one press.

    A directory that was already gone counts as removed: the outcome asked for is the outcome there
    is. One that is not empty, or that the filesystem refuses, is left alone and reported.
    """
    try:
        os.rmdir(target)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


@dataclass(frozen=True, slots=True)
class FolderCleared:
    """What a folder delete actually did, which is not always all of what was asked.

    `left_behind` is the honest half. Sift indexes media and walks past everything else, so a folder
    can hold subtitles, artwork, notes: files Sift never took and has no record of. Removing those
    would be deleting something nobody asked Sift to touch, with no bin and no way back, so the
    directory is left standing when anything remains in it and this says so. The caller tells
    somebody, rather than reporting a delete that half happened as a success.
    """

    #: How many copies had their bytes removed. Copies, not assets: a file that also sits in another
    #: folder loses the one in here and stays a file Sift knows about.
    files: int
    #: Directories removed from the disk, the folder itself included.
    directories: int
    #: Something Sift does not index is still in there, so the folder is still on the disk.
    left_behind: bool


@dataclass(frozen=True, slots=True)
class Removal:
    """What a delete over a selection came to."""

    removed: int
    skipped: int
    #: Why the first skip happened, worded for ONE file. See `kernel.reach.BulkWriteDone.reason`.
    reason: str | None
    #: The same reason worded for more than one, because only the screen knows how many there were.
    #:
    #: A refusal raised per file (a read-only folder, say) has ONE wording, and it is sent for
    #: both rather than left empty: it names no number, so it is true of one file and of thirty, and
    #: an empty plural would make the screen fall back to a singular against a plural count.
    reason_many: str | None
    vault_locked: bool


def _first_filename(locations: Sequence[Location]) -> str | None:
    """The name a file was known by, read off the places it sat, or None when it sat nowhere."""
    return locations[0].filename if locations else None


class Deleter:
    """The removal seam. One per application, reached at `app.state.deleter`.

    Everything that removes a file goes through `remove`. Nothing else in Sift unlinks, and a
    static rule in the build enforces that, so this class is the whole of the blast radius, and
    it is meant to stay small enough to read in one sitting.
    """

    def __init__(
        self,
        database: Database,
        content: ContentStore,
        library: LibraryStore,
        access: Repository,
        playback_cache: PlaybackCacheSeam,
        removed_members: RemovedMembersSeam,
    ) -> None:
        # The database is here for one thing only: writing down that a file was deleted. Nothing
        # in this class reads or writes a table through it (the rows are the content store's and
        # the grants are the access layer's) and a delete that left no trace would be a gap in
        # the record that nobody could fill from anywhere else, because there is nothing left to
        # look at afterwards.
        self._db = database
        self._content = content
        self._library = library
        self._access = access
        self._playback_cache = playback_cache
        # The scanner's memory, handed in: forgetting a picture inside an archive has to outlive the
        # next scan, since the archive still holds it and is not Sift's to change.
        self._removed_members = removed_members

    # --- the seam ---------------------------------------------------------------------------

    async def remove(
        self,
        asset_id: str,
        *,
        mode: Mode,
        actor: Viewer,
        location_id: str | None = None,
    ) -> None:
        """Remove an asset from the index, or its files from the disk as well.

        `location_id` filters the operation to one of the places an asset sits, which is what a
        caller wanting to clear a redundant copy needs; without it every location is taken. An
        asset that loses one of two locations is still an asset and keeps everything recorded
        about it. It is losing the last one that ends it.

        Raises `NotFound` if the user cannot see the asset, including when it does not exist,
        which is the same answer on purpose.
        """
        if await self._access.open_asset(actor, asset_id) is None:
            raise await self._unreachable(actor, asset_id)

        # Removing an asset from Sift is admin work, forget as much as disk. Forgetting drops the
        # shared index entry for everyone, not just the caller: it clears every location the asset
        # has (including copies in folders this user was never shown) and, once the last one
        # goes, cascades away the tags, people, collections and ratings attached to it. That is
        # library curation, and it is an admin's. The check sits after visibility so a guest naming
        # a file they may not see still gets the "no such file" 404 rather than a 403 that would
        # confirm it exists.
        if not actor.is_admin:
            raise NotAllowed("Only an admin can remove a file from Sift.")

        locations = await self._locations_for(asset_id, location_id)
        # Read while the rows are still there. A name looked up after the delete is nothing at
        # all, and the one question somebody brings to a record of deletions is WHAT went.
        name = locations[0].filename if locations else None
        # And what it was ON, for the same reason and at the same moment: every link row goes with
        # the file. Read before the mode is chosen rather than inside the branch, because both
        # tiers end the same way and a read in two places is a read one of them forgets. A delete
        # that turns out not to end the file pays for it and writes nothing, which is the price of
        # not being able to know that until the locations have gone.
        was_on = await self._was_on([asset_id])

        if mode == "sift":
            ended = await self._forget(asset_id, locations)
        else:
            ended = await self._delete_from_disk(asset_id, locations, actor=actor)
        if ended:
            archived = _inside_archives([asset_id], {asset_id: locations})
            await self._record_deletions(
                actor, [(asset_id, name)], was_on, removed_from=mode, archived=archived
            )

    async def remove_many(self, asset_ids: Sequence[str], *, mode: Mode, actor: Viewer) -> Removal:
        """Remove a selection, as one operation: what went, what did not, and the first reason.

        The same checks as `remove`, asked of the whole list at once, and the same order of work
        for each file: the disk first for a real delete, the index after. What is different is
        the bookkeeping: the index rows of every file that went are dropped in ONE write, the
        files that ended are ended in that write, their grants go in one more, and the change is
        announced once. Two or three transactions and two whole-table updates PER FILE would make
        a delete over a selection slow in proportion to its size and cold every picture in every
        browser twice a file.

        Still not a transaction over the disk, and it must not become one: bytes removed cannot be
        put back, so a selection that fails part way has genuinely removed what it removed, and
        the counts say so.
        """
        if not actor.is_admin:
            raise NotAllowed("Only an admin can remove a file from Sift.")
        standing = await self._access.actionable_of(actor, list(dict.fromkeys(asset_ids)))
        removed = 0
        skipped = len(standing.refused)
        reason: str | None = OUT_OF_REACH if standing.refused else None
        many: str | None = OUT_OF_REACH_MANY if standing.refused else None
        locked = False
        if standing.concealed:
            skipped += len(standing.concealed)
            reason, many = VAULT_LOCKED, VAULT_LOCKED_MANY
            locked = True
        places = await self._content.locations_of(standing.allowed)
        pictures = await self._content.derivatives_of(standing.allowed)
        # Read here with the other two, while every row is still there. See `_was_on`.
        was_on = await self._was_on(standing.allowed)
        to_forget: list[Location] = []
        gone: list[str] = []
        for asset_id in standing.allowed:
            found = places.get(asset_id, [])
            if mode == "disk":
                # Every folder is checked before the first file goes, per file, exactly as the
                # single form does it: a read-only folder refuses this file and leaves the rest.
                try:
                    resolved = [(one, await self._writable_path(one)) for one in found]
                except DeleteRefused as no:
                    skipped += 1
                    if reason is None or not locked:
                        # One wording for both numbers: a refusal like "that folder is read-only"
                        # names no count, so it is as true of thirty files as of one.
                        reason = reason if locked else str(no)
                        many = many if locked else _said_of_many(no)
                    continue
                for _location, path in resolved:
                    await asyncio.to_thread(_delete_file, path)
            to_forget.extend(found)
            gone.append(asset_id)
            removed += 1
        ended = await self._forget_selection(to_forget)
        if ended:
            await self._access.forget_items(ended)
            for asset_id in ended:
                await self._drop_pictures(pictures.get(asset_id, []))
                self._playback_cache.discard_asset(asset_id)
            await self._record_deletions(
                actor,
                [(asset_id, _first_filename(places.get(asset_id, []))) for asset_id in ended],
                was_on,
                removed_from=mode,
                archived=_inside_archives(ended, places),
            )
        log.info(
            "delete.many",
            mode=mode,
            removed=removed,
            ended=len(ended),
            skipped=skipped,
            locked=locked,
        )
        return Removal(
            removed=removed,
            skipped=skipped,
            reason=reason,
            reason_many=many,
            vault_locked=locked,
        )

    # --- a whole folder ---------------------------------------------------------------------

    async def remove_folder(self, folder_id: str, *, actor: Viewer) -> FolderCleared:
        """Delete a folder from the disk: every file Sift indexed under it, then the directories.

        The bytes only. The ROWS are the library service's, which is what already drops the grants
        naming a folder before the folder goes: the same order, and the same reason, as removing
        a whole library. Splitting it that way is not tidiness: this file is the one thing in Sift
        allowed to change a file somebody else put there, and it stays that size by not also owning
        the bookkeeping.

        ## What it refuses, and why each one is a refusal rather than a partial run

        A LIBRARY folder is refused outright. It is not inside anything Sift may write to, and
        "delete" on one means "stop reading this library", which is a different act with its own
        confirmation on the Folders screen and which touches no file at all.

        A file under it that this user cannot SEE is refused before anything is removed. That is
        the whole point of the concealment rule: deleting a folder whose contents you were not shown
        would remove files nobody ever put in front of you, and there is no bin to take them out of.

        A folder the filesystem will not let Sift change is refused before the first byte goes, for
        the reason `_delete_from_disk` gives one file at a time: a run that removed three folders and
        then found the fourth read-only would leave a half-done delete with nothing to name it.

        ## What it does NOT refuse

        Something Sift does not index sitting in the folder. Those are files Sift never took and has
        no record of (artwork, subtitles, somebody's notes) and removing them would be deleting
        what was never asked for. The indexed files still go; the directory is left standing and
        `FolderCleared.left_behind` says so, so the caller can tell somebody rather than reporting a
        success that was not one.
        """
        # Seen before allowed, the same order and for the same reason as `remove` above: somebody
        # who cannot see a folder is told it is not there rather than that they may not delete it.
        if await self._access.get_folder(actor, folder_id) is None:
            raise NotFound("There is no such folder.")
        if not actor.is_admin:
            raise NotAllowed("Only an admin can delete a folder.")

        folder = await self._library.get_folder(folder_id)
        if folder is None:
            raise NotFound("There is no such folder.")
        if folder.parent_id is None:
            raise DeleteRefused(
                "That is one of your library folders. Sift can stop reading it, on the Folders "
                "screen, which leaves every file exactly where it is."
            )

        try:
            root = await require_root(self._library, folder.root_id)
            base = Path(root.abs_path)
            # The folder itself, put back at the end of its own subtree.
            #
            # `folders_in_subtree` is strictly UNDER a path for every folder but one: a subtree is
            # found by prefix, the prefix is the path plus a slash, and `sets` never matches `sets/`.
            # Only a library ROOT, whose path is the empty string, is in its own answer, so the
            # id filter is what stops that one being listed twice, and this line is what stops every
            # other folder being deleted from the inside out and left standing.
            #
            # Deepest first, so a directory is only ever removed once what is inside it has gone.
            under = sorted(
                [
                    row
                    for row in await self._library.folders_in_subtree(
                        folder.root_id, folder.rel_path
                    )
                    if row.id != folder.id
                ]
                + [folder],
                key=lambda row: row.rel_path.count("/"),
                reverse=True,
            )
            directories = [
                (row, await asyncio.to_thread(confine, base, base / row.rel_path)) for row in under
            ]
            for _, directory in directories:
                await check_may_change(directory)
        except LibraryWriteRefused as refusal:
            raise DeleteRefused(str(refusal)) from refusal

        wanted = await self._files_under(directories, actor=actor)

        # What each file was called and what it was ON, read while the rows are still there: the
        # same two reads, at the same moment and for the same reason, as `remove` above. A folder
        # delete ends each file through `_end_if_unplaced`, which does not reach
        # `_record_deletions`, so without this a folder of files would go and no line would say so.
        names: dict[str, str | None] = {}
        for location, _ in wanted:
            names.setdefault(location.asset_id, location.filename)
        was_on = await self._was_on(list(names))

        touched: set[str] = set()
        for location, path in wanted:
            await asyncio.to_thread(_delete_file, path)
            await self._content.remove_location(location.id)
            touched.add(location.asset_id)
        ended: list[tuple[str, str | None]] = []
        for asset_id in sorted(touched):
            if await self._end_if_unplaced(asset_id):
                ended.append((asset_id, names.get(asset_id)))
        # One event per file that ENDED: a file with a copy outside this folder is still in the
        # library, and saying it was deleted would be a line describing what did not happen, with
        # the folder as each one's object: what the delete was done WITH, and how the folder's own
        # side of the record finds them.
        await self._record_deletions(
            actor,
            ended,
            was_on,
            within=Object(kind="folder", id=folder.id, name=folder.rel_path or folder.name),
            # A folder delete is always the disk's: the rows go with the bytes, never alone.
            removed_from="disk",
        )

        removed = 0
        for row, directory in directories:
            if not await asyncio.to_thread(_remove_directory, directory):
                continue
            removed += 1
            # Row by row, as each directory goes, rather than one cascading removal at the end: a
            # directory that could not be removed keeps its row, so what Sift lists and what is on
            # the disk agree even when only part of the tree came away.
            await forget_folder(self._library, self._access, row.id)
        cleared = FolderCleared(
            files=len(wanted),
            directories=removed,
            left_behind=removed < len(directories),
        )
        log.info(
            "delete.folder",
            folder_id=folder_id,
            files=cleared.files,
            directories=cleared.directories,
            left_behind=cleared.left_behind,
        )
        return cleared

    async def _files_under(
        self, directories: Sequence[tuple[FolderRow, Path]], *, actor: Viewer
    ) -> list[tuple[Location, Path]]:
        """Every file a folder delete removes, resolved and checked before the first one goes."""
        wanted: list[tuple[Location, Path]] = []
        for row, _ in directories:
            for location in await self._library.locations_in_folder(row.id):
                if location.inside_an_archive:
                    # Left with its archive, which Sift does not change: the archive keeps the
                    # directory standing, and `left_behind` says so.
                    continue
                if await self._access.open_asset(actor, location.asset_id) is None:
                    raise DeleteRefused(
                        "There is something in that folder that is hidden from you. Unlock the "
                        "vault and try again, so nothing is deleted that you were not shown."
                    )
                wanted.append((location, await self._writable_path(location)))
        return wanted

    async def _unreachable(self, actor: Viewer, asset_id: str) -> DeleteRefused:
        """Why this file could not be reached, as the refusal to raise.

        Asked only once the answer is known to be no, so nothing that succeeds pays for the second
        read: the same arrangement, and the same reasoning, as `kernel.reach.refuse_one`, which
        this cannot use because that one builds an HTTP exception and a service must not.

        The undifferentiated answer stays the default and stays first in the reader's mind: a file
        somebody was never shown and a file that is not there wear one face on purpose. The vault
        is the single exception, and it is the asker's own.
        """
        if await conceals(self._access, actor, asset_id):
            return VaultLocked(VAULT_LOCKED)
        return NotFound(OUT_OF_REACH)

    async def _locations_for(self, asset_id: str, location_id: str | None) -> list[Location]:
        locations = await self._content.locations(asset_id)
        if location_id is None:
            return locations
        chosen = [location for location in locations if location.id == location_id]
        if not chosen:
            raise NotFound(OUT_OF_REACH)
        return chosen

    # --- tier one: forget it ----------------------------------------------------------------

    async def _forget(self, asset_id: str, locations: list[Location]) -> bool:
        """Drop the index entries. No file is opened, moved, or removed.

        This is the tier that has to be completely safe, and the thing that makes it safe is that
        nothing in it touches a filesystem at all.

        True when the file itself ended, which is what the caller records. A file that sat in two
        folders and lost one is still the same file, and saying it was deleted would be a line in
        the record describing something that did not happen.
        """
        # Remembered before the row goes: the other order can lose the memory and keep the removal,
        # which the next scan undoes.
        await self._remember_members(locations)
        for location in locations:
            await self._content.remove_location(location.id)

        removed_asset = await self._end_if_unplaced(asset_id)
        log.info(
            "delete.forgotten",
            asset_id=asset_id,
            locations=len(locations),
            asset_removed=removed_asset,
        )
        return removed_asset

    async def _remember_members(self, locations: Sequence[Location]) -> None:
        """Tell the scanner to leave out every one of these that is a picture inside an archive.

        An ordinary file forgotten here comes back on the next scan, which is its way back (Delete
        from disk is its way out). A picture inside an archive has no other way out, so its removal
        is written where the scan reads it, with Try again under Skipped as the way back.
        """
        for location in locations:
            if location.inside_an_archive:
                await self._removed_members.remember_removed(
                    root_id=location.root_id,
                    rel_path=location.rel_path,
                    size_bytes=location.size_bytes or 0,
                )

    async def _forget_selection(self, locations: Sequence[Location]) -> list[str]:
        """Drop a selection's index rows in one write, remembering its archive members first (the
        same order as `_forget`, for the same reason); a disk delete has refused those already."""
        await self._remember_members(locations)
        return await self._content.forget_locations([one.id for one in locations])

    async def inside_archives(self, asset_ids: Sequence[str], *, actor: Viewer) -> int:
        """How many of these files this user can see sit inside an archive, so the screen draws
        Delete from disk unavailable before anybody presses it."""
        standing = await self._access.actionable_of(actor, list(dict.fromkeys(asset_ids)))
        places = await self._content.locations_of(standing.allowed)
        return len(_inside_archives(standing.allowed, places))

    # --- tier two: remove the bytes ---------------------------------------------------------

    async def _delete_from_disk(
        self, asset_id: str, locations: list[Location], *, actor: Viewer
    ) -> bool:
        # Every folder is checked before the first file goes. A delete that removed three of four
        # files and then discovered the fourth folder was read-only would leave somebody with a
        # half-done operation and no way to name what happened, and with no bin, no way back to
        # the state before it.
        resolved = [(location, await self._writable_path(location)) for location in locations]

        for location, path in resolved:
            await asyncio.to_thread(_delete_file, path)
            await self._content.remove_location(location.id)

        removed_asset = await self._end_if_unplaced(asset_id)
        log.info(
            "delete.from_disk",
            asset_id=asset_id,
            locations=len(locations),
            asset_removed=removed_asset,
        )
        return removed_asset

    # --- what went ----------------------------------------------------------------------------

    async def _was_on(self, asset_ids: Sequence[str]) -> WasOn:
        """The people, tags, Sites and shelves each of these files is on, in the file's own order.

        ASKED BEFORE ANYTHING IS REMOVED, which is the whole reason it is a read of its own rather
        than something the recording does at the end. Every one of these link rows carries
        `ON DELETE CASCADE`, so by the time the event is written the file is on nothing at all and
        the answer would be an empty list for every file: the same mistake the file's own name
        would be if it were looked up afterwards.

        One statement for the whole selection rather than one per file: a delete over a thousand
        files is four index seeks, not four thousand. See `_WAS_ON` for the order and for why the
        Sites are deduplicated.

        AND WHAT EACH FILE WAS, for the same reason and at the same moment: its size, its kind,
        when it arrived, how long it was, its song, and every person, tag, Site and Collection it
        was on (all of them, not the seven the event can name), each with the name it had then.
        A deleted file's page is gone and so is every row Insights could have asked, so the
        History line is the one place "how much did I clear" and "what went" can still be read.
        """
        if not asset_ids:
            return WasOn({}, {})
        wanted = json.dumps(list(asset_ids))
        found: dict[str, list[Subject]] = {}
        for row in await self._db.fetch_all(_WAS_ON, (wanted,)):
            found.setdefault(str(row["asset_id"]), []).append(
                Subject(
                    kind=cast("SubjectKind", str(row["kind"])),
                    id=str(row["id"]),
                    name=str(row["name"]),
                )
            )
        songs = {
            str(row["asset_id"]): {"id": str(row["id"]), "name": str(row["name"])}
            for row in await self._db.fetch_all(_SONG_OF, (wanted,))
        }
        facts: dict[str, dict[str, object]] = {}
        for asset_id in asset_ids:
            asset = await self._content.get(asset_id)
            if asset is None:  # pragma: no cover (just read by each caller; a concurrent delete)
                continue
            lists: dict[str, list[dict[str, str | None]]] = {}
            for one in found.get(asset_id, []):
                lists.setdefault(_LISTED[one.kind], []).append({"id": one.id, "name": one.name})
            facts[asset_id] = {
                "size": asset.size_bytes,
                "kind": asset.media_type,
                "arrived": asset.added_at,
                "length_ms": asset.duration_ms,
                "song": songs.get(asset_id),
                **lists,
            }
        return WasOn(found, facts)

    async def _record_deletions(
        self,
        actor: Viewer,
        gone: Sequence[tuple[str, str | None]],
        was_on: WasOn,
        *,
        removed_from: Mode,
        within: Object | None = None,
        archived: Sequence[str] = (),
    ) -> None:
        """Write down every file that ended, by the name it had, and whether the disk lost it.

        `archived` names the files that were pictures inside an archive: forgotten, and the archive
        left as it was, which the line says (`{"archive": true}`) so "from Sift only" is not read
        as a file still sitting loose on the disk.

        `within` is the folder a folder delete removed, as each event's OBJECT; None for a file or a
        selection deleted on its own, which is an act done with nothing.

        One event per file rather than one per selection, because the record is read from a file's
        own page as often as from the feed and a single event naming thirty files would appear on
        none of them: the thirty are gone, so there is no page left to draw it on. They share one
        transaction, which is what stops a selection delete costing a transaction a file.

        A name and not a link. Everything that could resolve the id has already cascaded away, so
        the snapshot in the row IS the answer to "what did I delete": there is nowhere else left
        to look.

        IT NAMES WHAT THE FILE WAS ON AS WELL, and that is what puts the line on those pages. An
        event is found by the things it names, so a delete naming only the file would appear only on
        the record of something that no longer has one: a person could not be told that a file of
        theirs had gone. The entities are subjects and not the object because the object is what an
        act was done WITH, and none of them was: they are what the act was about, which is the
        side a page is found from.

        Seven of them at most, the file first. The door refuses more than `MOST_SUBJECTS` for a
        reason written there, and the honest thing to do with a file on twenty people is to name
        the first seven and say how many were left rather than to write twenty rows or to write
        none. `{"more": n}` is that number, and the line the reader builds says it out loud.

        WHETHER THE FILE LEFT THE DISK, as `{"from": "disk"}` or `{"from": "sift"}`, on every one.
        The two acts share a verb because both end the file in the library, and they are not the
        same act to the person reading about it: one removed bytes nobody can put back, the other
        only stopped Sift listing a file that is still sitting where it was. Asked by keyword with
        no default, so a new delete path cannot record without saying which it was. The word is the
        `Mode` the deleter was handed, the same two words the route and the confirmation use. WHICH
        seven is the file's own order (see `_WAS_ON`), so it is the same seven every time rather
        than whichever the planner returned first.

        AND WHAT THE FILE WAS (`WasOn.facts`): its size, kind, arrival, length and song, and the
        whole of what it was on with the names they had. See `_was_on`.

        It ANNOUNCES, although the removal it describes has already been announced by the writes
        that performed it. The two are different news for different screens: those told the grids
        that the files have gone, and this tells the History pane that there is a new line saying
        so. That pane re-reads on the library's bell alone, so without this an admin watching it
        while somebody else deletes a selection sees nothing happen at all. `telling` says nothing
        when no row moved, so the early return above is the only empty case and it costs nothing.
        """
        if not gone:
            return
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            for asset_id, name in gone:
                on = was_on.on.get(asset_id, [])
                named = on[:MOST_ENTITIES]
                left = len(on) - len(named)
                said: dict[str, object] = {"from": removed_from}
                if asset_id in archived:
                    said["archive"] = True
                if left:
                    said["more"] = left
                said.update(was_on.facts.get(asset_id, {}))
                await record_event(
                    connection,
                    actor=Actor.user(actor.id),
                    verb="deleted",
                    subject=[Subject(kind="asset", id=asset_id, name=name), *named],
                    object=within,
                    payload=json.dumps(said),
                )

    # --- the end of an asset ----------------------------------------------------------------

    async def _end_if_unplaced(self, asset_id: str) -> bool:
        """Remove the asset once nothing points at it, and take its access grants with it.

        Both tiers finish here, and that is the point of it being one method rather than two
        matching pairs of lines. A grant naming an item carries no foreign key: it names a
        different table depending on what kind of thing it points at, so the database cannot
        cascade it, which means the grants outlive the item unless something deliberately
        removes them. Every other deletable thing a grant can name does this too, and for the
        item the leak would be the worst-shaped one of the set: a share that survives its item
        goes on applying to whatever id is issued next, and a restrict that survives is a promise
        quietly stopped being kept.

        Only when the asset actually ends. An asset that sits in two folders and loses one is
        still the same asset with the same grants, and dropping them there would revoke a share
        nobody asked to revoke, so this hangs off the answer the content store gives about
        whether the row went, not off the delete having been attempted.
        """
        # Read before the row goes: the pictures are listed against the asset and the database
        # takes that list with it.
        pictures = await self._content.derivatives(asset_id)

        removed = await self._content.remove_asset_if_unplaced(asset_id)
        if removed:
            await self._access.forget_object(ObjectType.ITEM, asset_id)
            await self._drop_pictures(pictures)
            # The transcoded pieces of a video are the same case as the pictures and are dropped on
            # the same terms. Not gated on which mode was asked for: a forgotten file re-scanned
            # later is a NEW asset with a new id, so its old pieces are unreachable either way.
            self._playback_cache.discard_asset(asset_id)
        return removed

    async def _drop_pictures(self, pictures: list[Derivative]) -> None:
        """The thumbnails, previews and sprite sheets made from a file that has just ended.

        Best effort, and that is the whole design of it. The cache is the disposable half of Sift
        and a delete must not come to depend on it being writable: a picture that cannot be
        unlinked is left where it is, the delete still succeeded, and the leftover sweep on the
        maintenance screen finds it later exactly as it finds every other unclaimed file down
        there.

        Done here rather than left entirely to that sweep because somebody who chose "delete from
        disk" meant it. Waiting for a maintenance pass leaves pictures of the file in the cache:
        unreachable, but still on the disk they asked to have it taken off.
        """
        for picture in pictures:
            with contextlib.suppress(Exception):
                path = await self._content.derivative_at(picture.rel_cache_path)
                if path is not None:
                    await asyncio.to_thread(path.unlink)

    async def _writable_path(self, location: Location) -> Path:
        """Where this location's file is, having proved Sift is allowed to change it.

        The decision itself is the kernel's (one door, `check_may_change`) and this only turns
        its refusal into a delete's, so there is no second copy of the decision here.

        A picture inside an archive is refused before anything is asked: `path_of` would hand back
        the copy pulled out into the cache, which IS writable, and deleting it changes nothing.
        """
        if location.inside_an_archive:
            raise InsideAnArchive(INSIDE_AN_ARCHIVE)
        try:
            await require_root(self._library, location.root_id)
            path = await self._content.path_of(location)
            await check_may_change(path.parent)
        except LibraryWriteRefused as refusal:
            raise DeleteRefused(str(refusal)) from refusal
        return path


#: The one thing in Sift that removes a file.
DELETER: Part[Deleter] = Part("deleter")
