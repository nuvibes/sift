# SPDX-License-Identifier: AGPL-3.0-or-later
"""The roots someone has pointed Sift at, and the folder rows inside them.

Roots never overlap and never hold Sift's own directories; Sift never writes into one."""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.cache_stamp import bump_cache_stamp
from sift.kernel.changes import About, announce, announce_arrival, telling
from sift.kernel.config import Settings
from sift.kernel.content.identity import Location, check_rel_path, location_from_row
from sift.kernel.db import Connection, Database, Row, in_clause, point_read
from sift.kernel.ids import is_id, new_id
from sift.kernel.ledger import Actor, record_event
from sift.kernel.log import get_logger
from sift.kernel.paths import is_writable, mount_is_readonly
from sift.kernel.text import clean_stored_text
from sift.kernel.vocabulary import Subject

log = get_logger(__name__)

ROOT_REL_PATH = ""

MAX_NAME_LENGTH = 100


class RootKind(StrEnum):
    """Where a root's files live: how presence is asked, not how it is watched; a test pins it."""

    LOCAL = "local"
    NAS = "nas"
    OTHER = "other"


class LibraryError(ValueError):
    """A root or folder that cannot exist. The message is written to be shown to a person."""


class RootOverlap(LibraryError):
    """The candidate root contains, sits inside, or is an existing one."""


class ReservedPath(LibraryError):
    """The candidate root is, contains, or sits inside a directory Sift writes to."""


class NotAFolder(LibraryError):
    """The path is not a directory Sift can read."""


class NotWritable(LibraryError):
    """Sift was asked to manage the files in a folder it cannot write to."""


@dataclass(frozen=True, slots=True)
class Root:
    id: str
    name: str
    abs_path: str
    kind: RootKind
    created_at: int


@dataclass(frozen=True, slots=True)
class Grant:
    """A folder handed to Sift through the system's dialog: permission to look, nothing else."""

    id: str
    abs_path: str
    granted_at: int


@dataclass(frozen=True, slots=True)
class FolderRow:
    """A folder as stored; the access layer's `Folder` is this as a viewer may see it."""

    id: str
    root_id: str
    parent_id: str | None
    rel_path: str
    name: str
    #: The directory's timestamp at the last walk, so the start catch-up costs folders, not files.
    seen_mtime: float | None = None


def root_from_row(row: Row) -> Root:
    return Root(
        id=row["id"],
        name=row["name"],
        abs_path=row["abs_path"],
        kind=RootKind(row["kind"]),
        created_at=row["created_at"],
    )


#: Another use of a granted folder (the backup folder), answering the folders it uses now.
GrantUser = Callable[[], Awaitable[Sequence[Path]]]


def grant_from_row(row: Row) -> Grant:
    return Grant(id=row["id"], abs_path=row["abs_path"], granted_at=row["granted_at"])


def folder_from_row(row: Row) -> FolderRow:
    return FolderRow(
        id=row["id"],
        root_id=row["root_id"],
        parent_id=row["parent_id"],
        rel_path=row["rel_path"],
        name=row["name"],
        seen_mtime=row["seen_mtime"],
    )


def overlaps(one: Path, other: Path) -> bool:
    """Whether two directories are the same, or one is inside the other, either way round."""
    return one == other or one in other.parents or other in one.parents


def check_name(name: str) -> str:
    """A root's name, as it will be shown. Not a path, and never used as one."""
    cleaned = name.strip()
    if not cleaned:
        raise LibraryError("Give this folder a name so you can tell it apart from the others.")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise LibraryError(f"That name is too long. Keep it under {MAX_NAME_LENGTH} characters.")
    if "\x00" in cleaned or "\n" in cleaned:
        raise LibraryError("A name cannot contain line breaks.")
    return cleaned


def resolve_directory(candidate: Path) -> Path:
    """The real directory a path names, symlinks resolved once, or a refusal; blocking."""
    if not candidate.is_absolute():
        raise NotAFolder(
            f"'{candidate}' is not a full path. Sift needs the whole path to the folder, "
            "starting from the top."
        )
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise NotAFolder(f"Sift cannot find the folder '{candidate}' ({exc.strerror}).") from exc

    if not resolved.is_dir():
        raise NotAFolder(
            f"'{candidate}' is a file, not a folder. Point Sift at the folder it sits in."
        )
    if not os.access(resolved, os.R_OK | os.X_OK):
        raise NotAFolder(
            f"Sift is not allowed to read the folder '{candidate}'. Check who owns it and who may "
            "read it. If Sift is running in a container, check that the folder is mounted into it."
        )
    return resolved


def check_not_reserved(candidate: Path, settings: Settings) -> None:
    """Refuse a root that is, holds, or sits inside a directory Sift writes to."""
    for owned in settings.managed_dirs:
        try:
            resolved = owned.resolve()
        except OSError:
            # Unreachable is a startup problem; a missing directory overlaps nothing.
            continue
        if overlaps(candidate, resolved):
            raise ReservedPath(
                "Sift keeps its own files in that folder, so it cannot also be a library folder. "
                "Pick a folder that holds only your media."
            )


def check_folder_writable(candidate: Path) -> None:
    """Refuse, in a sentence, a folder the filesystem will not let Sift write in now; blocking."""
    if mount_is_readonly(candidate):
        raise NotWritable(
            "This folder is read-only, so nothing can change anything in it \u2014 not Sift, and "
            "not any other program. That is decided by the disk it is on, or by the way it was "
            "handed over, and it cannot be changed from inside Sift."
        )
    if not is_writable(candidate):
        raise NotWritable(
            "Sift is not allowed to write in this folder, so it cannot delete or organize "
            "anything in it. Check who owns the folder and that Sift's account may write to it."
        )


def _folder_chain(rel_path: str) -> list[str]:
    """Every folder a path implies, outermost first: a/b/c -> [a, a/b, a/b/c]."""
    segments = check_rel_path(rel_path).split("/")
    return ["/".join(segments[: index + 1]) for index in range(len(segments))]


_INSERT_ROOT = """
INSERT INTO library_roots
  (id, name, abs_path, kind, created_at)
VALUES (?, ?, ?, ?, ?)
RETURNING *
"""

_INSERT_GRANT = """
INSERT INTO browse_grants (id, abs_path, granted_at)
VALUES (?, ?, ?)
RETURNING *
"""

_GRANTS = "SELECT * FROM browse_grants ORDER BY abs_path"
_DELETE_GRANT = "DELETE FROM browse_grants WHERE id = ? RETURNING *"

_ROOTS = "SELECT * FROM library_roots ORDER BY id"
_ROOTS_READ = point_read("content.roots", _ROOTS)
_ROOT_BY_ID = "SELECT * FROM library_roots WHERE id = ?"
_DELETE_ROOT = "DELETE FROM library_roots WHERE id = ? RETURNING *"

_LOCATED_UNDER_ROOT = "SELECT DISTINCT asset_id FROM asset_locations WHERE root_id = ?"
#: Strands only files left with no place at all.
_STRAND = """
UPDATE assets SET stranded_at = ?
 WHERE id IN (SELECT value FROM json_each(?))
   AND NOT EXISTS (SELECT 1 FROM asset_locations l WHERE l.asset_id = assets.id)
"""

# An upsert, as most users never hide a library; `hidden_at` is cleared on the way out.
_SET_ROOT_HIDDEN = """
INSERT INTO root_user_state (root_id, user_id, hidden, hidden_at, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(root_id, user_id) DO UPDATE SET
    hidden     = excluded.hidden,
    hidden_at  = excluded.hidden_at,
    updated_at = excluded.updated_at
"""

_HIDDEN_ROOTS = "SELECT root_id FROM root_user_state WHERE user_id = ? AND hidden = 1"

# The name moves with the path: a library is called what its directory is called.
_REPOINT_ROOT = "UPDATE library_roots SET name = ?, abs_path = ? WHERE id = ? RETURNING *"

# The library's own folder row carries the same name; every other folder is relative to it.
_RENAME_ROOT_FOLDER = "UPDATE folders SET name = ? WHERE root_id = ? AND parent_id IS NULL"


# The no-op update makes the existing row come back, which DO NOTHING would not.
_INSERT_FOLDER = """
INSERT INTO folders (id, root_id, parent_id, rel_path, name)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(root_id, rel_path) DO UPDATE SET rel_path = excluded.rel_path
RETURNING *
"""

_FOLDER_BY_ID = "SELECT * FROM folders WHERE id = ?"
_FOLDER_AT = "SELECT * FROM folders WHERE root_id = ? AND rel_path = ?"
_FOLDER_IDS_IN_ROOT = "SELECT id FROM folders WHERE root_id = ? ORDER BY rel_path"
_DELETE_FOLDER = "DELETE FROM folders WHERE id = ? RETURNING *"

# Present only: a folder is recognised by what it still holds.
_LOCATIONS_IN_FOLDER = """
SELECT * FROM asset_locations
 WHERE folder_id = ? AND status = 'present'
"""

# A move rewrites five path columns in one transaction, one statement each, never assembled.
# `substr` is 1-based, so `:cut` is one past the old path and the folder's own row lands on the new.
_MOVE_FOLDERS = """
UPDATE folders
   SET rel_path = :new || substr(rel_path, :cut)
 WHERE root_id = :root
   AND (rel_path = :old OR substr(rel_path, 1, :prefix_length) = :prefix)
"""

# The moved folder's own name and parent; its descendants keep theirs.
_RENAME_FOLDER = "UPDATE folders SET name = :name, parent_id = :parent WHERE id = :id"

_MOVE_LOCATIONS = """
UPDATE asset_locations
   SET rel_path = :new || substr(rel_path, :cut)
 WHERE root_id = :root
   AND (rel_path = :old OR substr(rel_path, 1, :prefix_length) = :prefix)
"""

# An archive's pictures record the archive's path too.
_MOVE_ARCHIVE_LOCATIONS = """
UPDATE asset_locations
   SET archive_rel_path = :new || substr(archive_rel_path, :cut)
 WHERE root_id = :root
   AND archive_rel_path IS NOT NULL
   AND (archive_rel_path = :old OR substr(archive_rel_path, 1, :prefix_length) = :prefix)
"""

# A set made from an archive is found again by that archive's path.
_MOVE_PHOTO_SETS = """
UPDATE photo_sets
   SET archive_rel_path = :new || substr(archive_rel_path, :cut)
 WHERE archive_root_id = :root
   AND archive_rel_path IS NOT NULL
   AND (archive_rel_path = :old OR substr(archive_rel_path, 1, :prefix_length) = :prefix)
"""

# A re-made folder row takes back the present files lying directly in it that lost their folder.
_ADOPT_LOCATIONS = """
UPDATE asset_locations SET folder_id = ?
 WHERE root_id = ? AND folder_id IS NULL AND status = 'present'
   AND rel_path > ? AND rel_path < ?
   AND instr(substr(rel_path, ?), '/') = 0
"""

_SET_FOLDER_SEEN_MTIME = "UPDATE folders SET seen_mtime = ? WHERE id = ?"

_FOLDERS_UNDER = """
SELECT * FROM folders
 WHERE root_id = ?
   AND substr(rel_path, 1, ?) = ?
 ORDER BY rel_path
"""

# Keyset paged, as OFFSET slows and skips rows while the scanner writes; an empty prefix means
# the whole root, so one statement serves both and a folder scan never sweeps wider.
_LOCATIONS_IN_ROOT_AFTER = """
SELECT * FROM asset_locations
 WHERE root_id = ? AND status = 'present' AND id > ?
   AND substr(rel_path, 1, ?) = ?
 ORDER BY id
 LIMIT ?
"""


class LibraryStore:
    """The roots and the folder tree. One per database."""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._db = database
        self._settings = settings
        self._clock = clock
        #: What else keeps a grant in use, with the sentence a refused give-back says.
        self._grant_users: list[tuple[str, GrantUser]] = []

    def _now(self) -> int:
        return int(self._clock())

    # A grant confines the folder picker; whether Sift may change a folder is asked at the write.

    async def grants(self) -> list[Grant]:
        """Every folder handed to Sift, in the order a person reads them."""
        rows = await self._db.fetch_all(_GRANTS)
        return [grant_from_row(row) for row in rows]

    async def grant(self, abs_path: Path) -> Grant:
        """Record a folder given through the system's dialog; overlaps are refused, never merged."""
        resolved = await asyncio.to_thread(resolve_directory, abs_path)
        await asyncio.to_thread(check_not_reserved, resolved, self._settings)

        for existing in await self.grants():
            here = Path(existing.abs_path)
            if here == resolved:
                raise LibraryError("Sift has already been given that folder.")
            if overlaps(here, resolved):
                raise RootOverlap(
                    f"'{existing.abs_path}' has already been given to Sift, and these two folders "
                    "are inside one another. Remove that one first if you meant to change what "
                    "Sift can see."
                )

        rows = await self._write_shared(_INSERT_GRANT, (new_id(), str(resolved), self._now()))
        return grant_from_row(rows[0])

    async def revoke_grant(self, grant_id: str) -> Grant | None:
        """Take a folder back, unless a library or another registered use still sits in it."""
        if not is_id(grant_id):
            return None
        held = [g for g in await self.grants() if g.id == grant_id]
        if not held:
            return None
        inside = Path(held[0].abs_path)
        for why, path in await self._grant_uses():
            if overlaps(path, inside):
                raise LibraryError(why)
        rows = await self._write_shared(_DELETE_GRANT, (grant_id,))
        return grant_from_row(rows[0]) if rows else None

    # A grant follows what uses it, so granted folders are never a list kept by hand.

    def use_grants_for(self, why: str, user: GrantUser) -> None:
        """Register a use of granted folders; `why` says what to change before giving one back."""
        self._grant_users.append((why, user))

    async def ensure_grant(self, abs_path: Path) -> Grant:
        """The grant covering this folder, made when there is none, with `grant`'s refusals."""
        resolved = await asyncio.to_thread(resolve_directory, abs_path)
        for existing in await self.grants():
            here = Path(existing.abs_path)
            if here == resolved or here in resolved.parents:
                return existing
        return await self.grant(resolved)

    async def release_unused_grants(self) -> list[Grant]:
        """Give back every granted folder nothing uses any more. Answers what was given back."""
        uses = [path for _, path in await self._grant_uses()]
        released: list[Grant] = []
        for grant in await self.grants():
            here = Path(grant.abs_path)
            if any(overlaps(here, path) for path in uses):
                continue
            rows = await self._write_shared(_DELETE_GRANT, (grant.id,))
            released.extend(grant_from_row(row) for row in rows)
        if released:
            log.info("library.grants_released", count=len(released))
        return released

    async def _grant_uses(self) -> list[tuple[str, Path]]:
        """Every folder something uses, each with the sentence a refused give-back says."""
        uses = [
            (
                f"'{root.name}' is a library inside that folder. Remove the library first, "
                "then Sift can give the folder back.",
                Path(root.abs_path),
            )
            for root in await self.roots()
        ]
        for why, user in self._grant_users:
            uses.extend((why, Path(path)) for path in await user())
        return uses

    async def create_root(
        self,
        *,
        name: str,
        abs_path: Path,
        kind: RootKind = RootKind.LOCAL,
    ) -> Root:
        """Point Sift at a directory, with its folder row; overlaps are checked inside the write."""
        name = check_name(name)
        resolved = await asyncio.to_thread(resolve_directory, abs_path)
        await asyncio.to_thread(check_not_reserved, resolved, self._settings)

        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            for row in await connection.execute_fetchall(_ROOTS):
                if overlaps(resolved, Path(str(row["abs_path"]))):
                    raise RootOverlap(
                        f"Sift is already watching that folder as part of '{row['name']}'. "
                        "Library folders cannot sit inside one another, so add a folder that is "
                        f"outside '{row['name']}', or remove it first."
                    )

            rows = list(
                await connection.execute_fetchall(
                    _INSERT_ROOT,
                    (
                        new_id(),
                        name,
                        str(resolved),
                        kind.value,
                        self._now(),
                    ),
                )
            )
            root = root_from_row(rows[0])
            await self._insert_folder(
                connection, root_id=root.id, parent_id=None, rel_path=ROOT_REL_PATH, name=name
            )

        # The path names a real person's disk; the id says the same, so only it is logged.
        log.info("library.root_added", root_id=root.id, kind=root.kind.value)
        return root

    async def roots(self) -> list[Root]:
        rows = await self._db.fetch_all(_ROOTS_READ)
        return [root_from_row(row) for row in rows]

    async def get_root(self, root_id: str) -> Root | None:
        if not is_id(root_id):
            return None
        row = await self._db.fetch_one(_ROOT_BY_ID, (root_id,))
        return None if row is None else root_from_row(row)

    async def hidden_roots(self, user_id: str) -> set[str]:
        """Which libraries this user has hidden; per user, so never a flag on `Root`."""
        rows = await self._db.fetch_all(_HIDDEN_ROOTS, (user_id,))
        return {str(row["root_id"]) for row in rows}

    async def set_root_hidden(self, user_id: str, root_id: str, *, hidden: bool) -> None:
        """Hide a library, or bring it back, for this user only; permission is the route's."""
        now = self._now()
        # One transaction with the stamp, so no moment leaves a hidden library in the cache.
        async with self._db.write() as connection:
            await connection.execute(
                _SET_ROOT_HIDDEN, (root_id, user_id, int(hidden), now if hidden else None, now)
            )
            announce(await bump_cache_stamp(connection, user_id), About.LIBRARY)

    async def repoint_root(self, root_id: str, abs_path: Path) -> Root | None:
        """The same library at a new place on disk, with every check adding one makes."""
        if not is_id(root_id):
            return None
        resolved = await asyncio.to_thread(resolve_directory, abs_path)
        await asyncio.to_thread(check_not_reserved, resolved, self._settings)

        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            existing = list(await connection.execute_fetchall(_ROOT_BY_ID, (root_id,)))
            if not existing:
                return None

            for row in await connection.execute_fetchall(_ROOTS):
                if str(row["id"]) == root_id:
                    continue
                if overlaps(resolved, Path(str(row["abs_path"]))):
                    raise RootOverlap(
                        f"Sift is already watching that folder as part of '{row['name']}'. "
                        "Library folders cannot sit inside one another, so pick a folder that is "
                        f"outside '{row['name']}'."
                    )

            rows = list(
                await connection.execute_fetchall(
                    _REPOINT_ROOT,
                    (check_name(resolved.name or str(resolved)), str(resolved), root_id),
                )
            )
            root = root_from_row(rows[0])
            # The library's own folder row is named after the directory, so it moves with it.
            await connection.execute(_RENAME_ROOT_FOLDER, (root.name, root_id))

        log.info("library.root_repointed", root_id=root.id)
        return root

    async def delete_root(self, root_id: str, *, actor: Actor) -> bool:
        """Forget a root and everything indexed under it, keeping its assets; touches no file."""
        if not is_id(root_id):
            return False
        located = [
            str(row["asset_id"])
            for row in await self._db.fetch_all(_LOCATED_UNDER_ROOT, (root_id,))
        ]
        named = await self._db.fetch_one("SELECT name FROM library_roots WHERE id = ?", (root_id,))
        async with self._db.write() as connection:
            rows = list(await connection.execute_fetchall(_DELETE_ROOT, (root_id,)))
            if rows:
                await announce_arrival(connection)
                # The only record the library existed, with the count somebody comes back for.
                await record_event(
                    connection,
                    actor=actor,
                    verb="removed",
                    subject=Subject(
                        kind="folder",
                        id=root_id,
                        name=None if named is None else str(named["name"]),
                    ),
                    payload=json.dumps({"stranded": len(located)}),
                )
        if not rows:
            return False
        # Dated, so re-adding the folder brings everything back for a while (`stranded_asset_ids`).
        if located:
            await self._write(_STRAND, (int(time.time()), json.dumps(located)))
        log.info("library.root_removed", root_id=root_id, stranded=len(located))
        # After the delete: a failure here keeps a grant and must not fail the removal.
        try:
            await self.release_unused_grants()
        except Exception:
            log.exception("library.grants_release_failed", root_id=root_id)
        return True

    async def folder_ids_in_root(self, root_id: str) -> list[str]:
        """Every folder id under a root, as grants naming them do not cascade."""
        rows = await self._db.fetch_all(_FOLDER_IDS_IN_ROOT, (root_id,))
        return [str(row["id"]) for row in rows]

    async def upsert_folder(self, root_id: str, rel_path: str) -> FolderRow:
        """The folder at this path, creating it and every folder above; safe to run concurrently."""
        chain = _folder_chain(rel_path)
        async with self._db.write() as connection:
            parent = await self._require_root_folder(connection, root_id)
            for path in chain:
                parent = await self._insert_folder(
                    connection,
                    root_id=root_id,
                    parent_id=parent.id,
                    rel_path=path,
                    name=clean_stored_text(path.rsplit("/", 1)[-1]),
                )
            return parent

    async def root_folder(self, root_id: str) -> FolderRow | None:
        """The folder row standing for the root directory itself."""
        return await self.folder_at(root_id, ROOT_REL_PATH)

    async def folder_at(self, root_id: str, rel_path: str) -> FolderRow | None:
        """The folder at this path, or None; a read that never opens a write."""
        row = await self._db.fetch_one(_FOLDER_AT, (root_id, rel_path))
        return None if row is None else folder_from_row(row)

    async def get_folder(self, folder_id: str) -> FolderRow | None:
        """A folder row with no permission check, for callers acting for nobody."""
        if not is_id(folder_id):
            return None
        row = await self._db.fetch_one(_FOLDER_BY_ID, (folder_id,))
        return None if row is None else folder_from_row(row)

    async def folders_under(self, folder: FolderRow) -> list[FolderRow]:
        """A folder's descendants, itself excluded."""
        prefix = subtree_prefix(folder.rel_path)
        rows = await self._db.fetch_all(_FOLDERS_UNDER, (folder.root_id, len(prefix), prefix))
        return [folder_from_row(row) for row in rows if row["rel_path"] != folder.rel_path]

    async def folders_in_subtree(self, root_id: str, under: str) -> list[FolderRow]:
        """Every folder row under a path; `under` itself is left out unless it is the root."""
        prefix = subtree_prefix(under)
        rows = await self._db.fetch_all(_FOLDERS_UNDER, (root_id, len(prefix), prefix))
        return [folder_from_row(row) for row in rows]

    async def locations_in_folder(self, folder_id: str) -> list[Location]:
        """The present locations directly inside one folder, never underneath it."""
        if not is_id(folder_id):
            return []
        rows = await self._db.fetch_all(_LOCATIONS_IN_FOLDER, (folder_id,))
        return [location_from_row(row) for row in rows]

    async def move_folder(
        self, folder: FolderRow, new_rel_path: str, *, actor: Actor
    ) -> FolderRow | None:
        """Move a folder in its library, keeping its id; five path columns move in one write."""
        new_rel_path = check_rel_path(new_rel_path)
        old = folder.rel_path
        if old == ROOT_REL_PATH:
            # The library's own row moves only with the whole library.
            return None

        taken = await self._db.fetch_one(_FOLDER_AT, (folder.root_id, new_rel_path))
        if taken is not None:
            return None

        # The new parent row must exist already: the caller makes the chain, never this method.
        above = new_rel_path.rsplit("/", 1)[0] if "/" in new_rel_path else ROOT_REL_PATH
        holder = await self._db.fetch_one(_FOLDER_AT, (folder.root_id, above))
        if holder is None:
            return None

        prefix = old + "/"
        moved = {
            "root": folder.root_id,
            "old": old,
            "prefix": prefix,
            "prefix_length": len(prefix),
            "cut": len(old) + 1,
            "new": new_rel_path,
        }
        renamed = {
            "name": clean_stored_text(new_rel_path.rsplit("/", 1)[-1]),
            "parent": str(holder["id"]),
            "id": folder.id,
        }

        async with self._db.write() as connection:
            await connection.execute(_MOVE_FOLDERS, moved)
            await connection.execute(_RENAME_FOLDER, renamed)
            await connection.execute(_MOVE_LOCATIONS, moved)
            await connection.execute(_MOVE_ARCHIVE_LOCATIONS, moved)
            await connection.execute(_MOVE_PHOTO_SETS, moved)
            await announce_arrival(connection)
            # The only record of where the folder was; the old path rides in the payload.
            await record_event(
                connection,
                actor=actor,
                verb="moved",
                subject=Subject(kind="folder", id=folder.id, name=new_rel_path),
                payload=json.dumps({"before": old}),
            )

        log.info("library.folder_moved", root_id=folder.root_id, folder_id=folder.id)
        return await self.get_folder(folder.id)

    async def record_folder_mtime(self, folder_id: str, mtime: float | None) -> None:
        """Remember a folder's directory timestamp at the walk; None when it could not be read."""
        if not is_id(folder_id):
            return
        await self._write(_SET_FOLDER_SEEN_MTIME, (mtime, folder_id))

    async def folders_in_root(self, root_id: str) -> list[FolderRow]:
        """Every folder row in one library, in one answer."""
        return await self.folders_in_subtree(root_id, ROOT_REL_PATH)

    async def remove_folder(self, folder_id: str) -> bool:
        """Forget one folder and its subtree, keeping its files; the caller drops grants first."""
        if not is_id(folder_id):
            return False
        async with self._db.write() as connection:
            rows = list(await connection.execute_fetchall(_DELETE_FOLDER, (folder_id,)))
            if rows:
                await announce_arrival(connection)
        if not rows:
            return False
        log.info("library.folder_removed", folder_id=folder_id)
        return True

    async def iter_locations_in_root(
        self, root_id: str, *, under: str = ROOT_REL_PATH, batch: int = 500
    ) -> AsyncIterator[Location]:
        """Every location believed present under `under`, a batch at a time in id order."""
        prefix = subtree_prefix(under)
        cursor = ""
        while True:
            rows = await self._db.fetch_all(
                _LOCATIONS_IN_ROOT_AFTER, (root_id, cursor, len(prefix), prefix, batch)
            )
            if not rows:
                return
            for row in rows:
                yield location_from_row(row)
            cursor = str(rows[-1]["id"])

    async def _require_root_folder(self, connection: Connection, root_id: str) -> FolderRow:
        rows = list(await connection.execute_fetchall(_FOLDER_AT, (root_id, ROOT_REL_PATH)))
        if not rows:
            raise LibraryError(f"library root {root_id} has no folder row")
        return folder_from_row(rows[0])

    async def _insert_folder(
        self,
        connection: Connection,
        *,
        root_id: str,
        parent_id: str | None,
        rel_path: str,
        name: str,
    ) -> FolderRow:
        rows = list(
            await connection.execute_fetchall(
                _INSERT_FOLDER, (new_id(), root_id, parent_id, rel_path, name)
            )
        )
        return folder_from_row(rows[0])

    async def adopt_locations(self, folder: FolderRow) -> None:
        """Give a folder row the present files directly in it that have no folder."""
        prefix = folder.rel_path + "/"
        await self._write_shared(
            _ADOPT_LOCATIONS,
            (folder.id, folder.root_id, prefix, folder.rel_path + "0", len(prefix) + 1),
        )

    async def _write(self, sql: str, params: tuple[object, ...]) -> list[Row]:
        async with self._db.write() as connection:
            return list(await connection.execute_fetchall(sql, params))

    async def _write_shared(self, sql: str, params: tuple[object, ...]) -> list[Row]:
        """One write to the granted folders, with every admin told once it lands."""
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            return list(await connection.execute_fetchall(sql, params))


def subtree_prefix(rel_path: str) -> str:
    """What every path inside a folder starts with; empty for the root's own folder."""
    return "" if rel_path == ROOT_REL_PATH else rel_path + "/"


# Names for the jobs dashboard, beside the tables, as only the kernel writes SQL against them.

_ASSET_NAMES = """
SELECT a.id,
       COALESCE(
         (SELECT l.filename FROM asset_locations l
           WHERE l.asset_id = a.id AND l.status = 'present'
           ORDER BY l.first_seen_at LIMIT 1),
         a.original_filename
       )
  FROM assets a
 WHERE a.id IN (?*)
"""

_ROOT_NAMES = "SELECT id, name FROM library_roots WHERE id IN (?*)"


async def names_for_assets(database: Database, asset_ids: Sequence[str]) -> dict[str, str]:
    """The filename each asset is known by, for the ids that still exist."""
    if not asset_ids:
        return {}
    query, params = in_clause(_ASSET_NAMES, list(asset_ids))
    return {str(row[0]): str(row[1]) for row in await database.fetch_all(query, params) if row[1]}


async def names_for_roots(database: Database, root_ids: Sequence[str]) -> dict[str, str]:
    """What each library folder is called. Same shape, and the same reason, as the assets above."""
    if not root_ids:
        return {}
    query, params = in_clause(_ROOT_NAMES, list(root_ids))
    return {str(row[0]): str(row[1]) for row in await database.fetch_all(query, params) if row[1]}
