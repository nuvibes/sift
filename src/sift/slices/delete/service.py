# SPDX-License-Identifier: AGPL-3.0-or-later
"""The only code in Sift that removes a file somebody else put there.

Two tiers, both admin-only: `mode="sift"` forgets the index entry and leaves the file, and
`mode="disk"` unlinks it with no way back. There is no bin, so every check happens before the first
file is touched, and a file the user cannot see is answered as not there.
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

#: How many things a file was on one delete event names: the door's ceiling, less the file.
MOST_ENTITIES: Final = MOST_SUBJECTS - 1

#: Everything a file was on, read before the link rows cascade away with it. `UNION` names a Site
#: once; the rank column keeps the file's own order and decides which seven are kept.
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


#: The song each file carries, read before its link goes. A file carries one song at most.
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
    """Everything a file was, read before it goes: what it was on and the facts Insights adds up."""

    on: dict[str, list[Subject]]
    facts: dict[str, dict[str, object]]


#: A picture inside an archive is not deleted from disk: Sift does not rewrite an archive, and
#: the next scan would bring the picture back.
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
    """A refusal said of a selection; most name no count, an archive's carries its own plural."""
    return refusal.many if isinstance(refusal, InsideAnArchive) else str(refusal)


class DeleteRefused(Exception):
    """A delete will not be carried out, and the message says why, for the person who pressed it."""


class NotFound(DeleteRefused):
    """There is nothing here to delete: no such asset and an unseen asset answer alike on purpose."""


class NotAllowed(DeleteRefused):
    """The user may see the file but may not do this to it."""


class InsideAnArchive(DeleteRefused):
    """A disk delete of a picture that lives inside an archive: refused, with what works instead."""

    #: The same refusal said of a selection, which only the bulk route has.
    many = INSIDE_AN_ARCHIVE_MANY


class VaultLocked(NotFound, ConcealedByVault):
    """The one refusal a person is owed the truth about: their own vault is concealing it."""


def _delete_file(target: Path) -> None:
    """Remove a file, treating one that is already gone as success."""
    with contextlib.suppress(FileNotFoundError):
        os.unlink(target)


def _remove_directory(target: Path) -> bool:
    """Remove one EMPTY directory, never a tree, so nothing Sift did not index goes. Says whether it
    went; never raises.
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
    """What a folder delete did; `left_behind` when files Sift does not index keep it standing."""

    #: Copies whose bytes were removed; a file with a copy elsewhere stays a file Sift knows about.
    files: int
    #: Directories removed from the disk, the folder itself included.
    directories: int
    left_behind: bool


@dataclass(frozen=True, slots=True)
class Removal:
    """What a delete over a selection came to."""

    removed: int
    skipped: int
    #: Why the first skip happened, worded for ONE file. See `kernel.reach.BulkWriteDone.reason`.
    reason: str | None
    #: The same reason worded for more than one; a per-file refusal names no count, so it is reused.
    reason_many: str | None
    vault_locked: bool


def _first_filename(locations: Sequence[Location]) -> str | None:
    """The name a file was known by, read off the places it sat, or None when it sat nowhere."""
    return locations[0].filename if locations else None


class Deleter:
    """The removal seam, reached at `app.state.deleter`; nothing else in Sift unlinks a file."""

    def __init__(
        self,
        database: Database,
        content: ContentStore,
        library: LibraryStore,
        access: Repository,
        playback_cache: PlaybackCacheSeam,
        removed_members: RemovedMembersSeam,
    ) -> None:
        # Only to record a deletion; afterwards there is nothing left to look at.
        self._db = database
        self._content = content
        self._library = library
        self._access = access
        self._playback_cache = playback_cache
        # The scanner's memory: forgetting a picture inside an archive must outlive the next scan.
        self._removed_members = removed_members

    async def remove(
        self,
        asset_id: str,
        *,
        mode: Mode,
        actor: Viewer,
        location_id: str | None = None,
    ) -> None:
        """Remove an asset from the index, or its files from the disk as well; `location_id` takes one
        copy only. Raises `NotFound` for an asset the user cannot see.
        """
        if await self._access.open_asset(actor, asset_id) is None:
            raise await self._unreachable(actor, asset_id)

        # Admin work in both tiers: forgetting drops a shared entry and what hangs off it. Checked
        # after
        # visibility, so an unseen file is still a 404.
        if not actor.is_admin:
            raise NotAllowed("Only an admin can remove a file from Sift.")

        locations = await self._locations_for(asset_id, location_id)
        # Read while the rows are still there: a name looked up afterwards is nothing at all.
        name = locations[0].filename if locations else None
        # What it was ON, read now for the same reason; every link row goes with the file.
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
        """Remove a selection as one operation: the same checks as `remove`, with the bookkeeping in
        one write. Not a transaction over the disk: what went has gone, and the counts say so.
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
        was_on = await self._was_on(standing.allowed)
        to_forget: list[Location] = []
        gone: list[str] = []
        for asset_id in standing.allowed:
            found = places.get(asset_id, [])
            if mode == "disk":
                # Checked per file: a read-only folder refuses this file and leaves the rest.
                try:
                    resolved = [(one, await self._writable_path(one)) for one in found]
                except DeleteRefused as no:
                    skipped += 1
                    if reason is None or not locked:
                        # Such a refusal names no count, so it is true of thirty files.
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

    async def remove_folder(self, folder_id: str, *, actor: Viewer) -> FolderCleared:
        """Delete a folder from the disk: every file Sift indexed under it, then the directories.

        Refused for a library folder, for a file under it the user cannot see, and for a folder Sift
        may not change, before anything goes. Files Sift does not index stay, and `left_behind` says
        so.
        """
        # Seen before allowed, as in `remove`.
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

        directories = await self._directories_of(folder)
        wanted = await self._files_under(directories, actor=actor)

        # Names and what each file was on, read while the rows are still there, as in `remove`.
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
        # One event per file that ENDED, with the folder as its object.
        await self._record_deletions(
            actor,
            ended,
            was_on,
            within=Object(kind="folder", id=folder.id, name=folder.rel_path or folder.name),
            removed_from="disk",
        )

        removed = await self._remove_directories(directories)
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

    async def _directories_of(self, folder: FolderRow) -> list[tuple[FolderRow, Path]]:
        """A folder and every folder under it, deepest first, each one checked as changeable."""
        try:
            root = await require_root(self._library, folder.root_id)
            base = Path(root.abs_path)
            # The folder itself, deepest first; only a library root is in its own
            # `folders_in_subtree` answer, hence the id filter.
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
        return directories

    async def _remove_directories(self, directories: Sequence[tuple[FolderRow, Path]]) -> int:
        """Remove the emptied directories, deepest first; how many went."""
        removed = 0
        for row, directory in directories:
            if not await asyncio.to_thread(_remove_directory, directory):
                continue
            removed += 1
            # Row by row, so a directory that stayed keeps its row and the listing matches the disk.
            await forget_folder(self._library, self._access, row.id)
        return removed

    async def _files_under(
        self, directories: Sequence[tuple[FolderRow, Path]], *, actor: Viewer
    ) -> list[tuple[Location, Path]]:
        """Every file a folder delete removes, resolved and checked before the first one goes."""
        wanted: list[tuple[Location, Path]] = []
        for row, _ in directories:
            for location in await self._library.locations_in_folder(row.id):
                if location.inside_an_archive:
                    # Left with its archive, which Sift does not change; `left_behind` says so.
                    continue
                if await self._access.open_asset(actor, location.asset_id) is None:
                    raise DeleteRefused(
                        "There is something in that folder that is hidden from you. Unlock the "
                        "vault and try again, so nothing is deleted that you were not shown."
                    )
                wanted.append((location, await self._writable_path(location)))
        return wanted

    async def _unreachable(self, actor: Viewer, asset_id: str) -> DeleteRefused:
        """Why this file could not be reached, asked only once the answer is no. Unseen and absent wear
        one face; the asker's own vault is the exception.
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

    async def _forget(self, asset_id: str, locations: list[Location]) -> bool:
        """Drop the index entries, touching no file. True when the file itself ended."""
        # Remembered before the row goes, or the next scan undoes the removal.
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
        """Tell the scanner to leave out these pictures inside an archive, the only way out they have."""
        for location in locations:
            if location.inside_an_archive:
                await self._removed_members.remember_removed(
                    root_id=location.root_id,
                    rel_path=location.rel_path,
                    size_bytes=location.size_bytes or 0,
                )

    async def _forget_selection(self, locations: Sequence[Location]) -> list[str]:
        """Drop a selection's index rows in one write, remembering its archive members first."""
        await self._remember_members(locations)
        return await self._content.forget_locations([one.id for one in locations])

    async def inside_archives(self, asset_ids: Sequence[str], *, actor: Viewer) -> int:
        """How many of these visible files sit inside an archive, so the screen can disable the button."""
        standing = await self._access.actionable_of(actor, list(dict.fromkeys(asset_ids)))
        places = await self._content.locations_of(standing.allowed)
        return len(_inside_archives(standing.allowed, places))

    async def _delete_from_disk(
        self, asset_id: str, locations: list[Location], *, actor: Viewer
    ) -> bool:
        # Every folder is checked before the first file goes: with no bin, half done is final.
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

    async def _was_on(self, asset_ids: Sequence[str]) -> WasOn:
        """What each file is on and what it was (`WasOn`), read before anything is removed, since every
        link row cascades away. One statement for the whole selection.
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
        """Write one event per file that ended, by its name, naming what it was on and which tier.

        Seven subjects at most, the file first, with `{"more": n}`; `{"from": ...}` says whether the
        disk lost it, and `archived` marks a picture inside an archive. It announces, so History re-
        reads.
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

    async def _end_if_unplaced(self, asset_id: str) -> bool:
        """Remove the asset once nothing points at it, and take its access grants with it.

        A grant has no foreign key to cascade, and one outliving its item would apply to the next
        id.
        """
        # Read before the row goes, which takes the list with it.
        pictures = await self._content.derivatives(asset_id)

        removed = await self._content.remove_asset_if_unplaced(asset_id)
        if removed:
            await self._access.forget_object(ObjectType.ITEM, asset_id)
            await self._drop_pictures(pictures)
            # A video's transcoded pieces go on the same terms, in either mode.
            self._playback_cache.discard_asset(asset_id)
        return removed

    async def _drop_pictures(self, pictures: list[Derivative]) -> None:
        """The pictures made from a file that just ended, best effort: one left behind is swept later."""
        for picture in pictures:
            with contextlib.suppress(Exception):
                path = await self._content.derivative_at(picture.rel_cache_path)
                if path is not None:
                    await asyncio.to_thread(path.unlink)

    async def _writable_path(self, location: Location) -> Path:
        """Where this location's file is, once `check_may_change` allows it; an archive member is
        refused.
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
