# SPDX-License-Identifier: AGPL-3.0-or-later
"""The roots someone has pointed Sift at, and the folder rows inside them.

A root is a directory on a disk. Sift indexes what is in it and never writes to it: the files
belong to the person running it, they stay exactly where they are, and the only directories Sift
writes to are its own. Two rules here are what keep that true, and both are enforced where a root
is created rather than left to the interface:

    roots do not overlap each other       so every folder has exactly one parent root, which is
                                          what makes an inherited permission unambiguous
    a root is not one of Sift's own       point a root at a parent of the cache directory and
    directories, and holds none of them   thumbnails start appearing beside the originals

Folders are rows rather than path strings because they carry the access rules, and a permission
that lives in a string cannot be joined against. Every root gets one folder row for the root
itself (no parent, empty path), so the tree always terminates somewhere and a file dropped on
a root has a real folder to land in.

Nothing here takes a viewer. This is the layer beneath the access rules, like the content store
beside it: what a particular person may see of a folder is resolved a layer up, by the only thing
that knows who is asking. What lives here is what the tables can do.
"""

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

#: The folder row standing for the root itself: its path relative to the root, so empty.
ROOT_REL_PATH = ""

MAX_NAME_LENGTH = 100


class RootKind(StrEnum):
    """Where a root's files actually live.

    It decides how the library list asks whether the folder is there (every share together, each
    held to its own timeout, where a local folder is one `stat`), and whether the list names the
    device a folder is on. It does NOT decide how the folder is watched: a share on Windows
    reports its own changes through the file server, so every root gets a native
    watch, and one that will not attach falls back to polling on that observation rather than on
    this word. See `watcher._observe`. Repeating the values of the CHECK constraint is unavoidable
    (SQLite takes no placeholder in one) and a test compares the two, so a drift is caught here
    rather than by an insert failing months later.
    """

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
    """A folder somebody handed to Sift through the operating system's own dialog.

    It is permission to LOOK, and nothing else. The folder picker lists only what is inside a grant,
    which is what confines it (see the schema for why that matters and why this carries no writable
    flag of its own).
    """

    id: str
    abs_path: str
    granted_at: int


@dataclass(frozen=True, slots=True)
class FolderRow:
    """A folder as it is stored.

    The access layer has its own `Folder`, which is this plus whether the viewer is being shown a
    concealed one. This is the row; that is the row as somebody is allowed to see it.
    """

    id: str
    root_id: str
    parent_id: str | None
    rel_path: str
    name: str
    #: The directory's own timestamp when a scan last walked it, or None for a folder no pass has
    #: recorded. See `schema._CREATE_FOLDERS`: this is what lets the catch-up at start cost the
    #: number of folders rather than the number of files.
    seen_mtime: float | None = None


def root_from_row(row: Row) -> Root:
    return Root(
        id=row["id"],
        name=row["name"],
        abs_path=row["abs_path"],
        kind=RootKind(row["kind"]),
        created_at=row["created_at"],
    )


#: Something besides the libraries that keeps a granted folder in use (the backup folder): asked
#: whenever a grant might be given back, it answers the folders it uses right now.
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


# --- the rules ------------------------------------------------------------------------------


def overlaps(one: Path, other: Path) -> bool:
    """Whether two directories are the same, or one is inside the other.

    Both directions, and the second one is the easy half to forget: adding a root inside an
    existing one is the obvious mistake, and adding a root that is the *parent* of an existing one
    is the same mistake written backwards. Either way a file would sit in two roots at the same
    time, and the question of which root's permissions apply to it would have two answers.
    """
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
    """The real directory a path names, or a refusal a person can act on.

    Symlinks are followed here, once, and the resolved path is what gets stored. Everything after
    this compares stored paths (whether two roots overlap, whether a root holds Sift's cache),
    and two paths that reach the same directory by different names would compare as different
    while behaving as the same. Blocking: it reads the disk, so it runs off the event loop.
    """
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
    """Refuse a root that is, holds, or sits inside a directory Sift writes to.

    Sift never writes into a library. That is a promise about behaviour, and this is what makes it
    a fact about the layout: with the cache directory inside a library root, thumbnails and
    transcoded segments would be written beside the originals by a system that is working exactly
    as designed. Both directions again: a root inside the cache is as wrong as a cache inside a
    root, and this is checked against the directories Sift actually creates rather than a list
    that would drift from them.
    """
    for owned in settings.managed_dirs:
        try:
            resolved = owned.resolve()
        except OSError:
            # It cannot be created or reached, which is a startup problem and not this one. A
            # directory that is not there cannot be overlapped by anything.
            continue
        if overlaps(candidate, resolved):
            raise ReservedPath(
                "Sift keeps its own files in that folder, so it cannot also be a library folder. "
                "Pick a folder that holds only your media."
            )


def check_folder_writable(candidate: Path) -> None:
    """Refuse, in a sentence, a folder the filesystem will not let Sift write in.

    Asked at the moment of a write (a delete, a rename, a move, a download landing) and never
    stored: a folder handed to Sift read-only cannot be written whatever anybody asks, and a
    folder writable today can be remounted read-only tomorrow. The two reasons are told apart
    because the ways out of them are different: a read-only folder has to be handed over
    differently, and a folder Sift has no permission in is a question of who owns it.

    No `managed` flag (consent recorded per folder) is asked: handing Sift a folder is the
    permission, and what protects a file is the confirmation at the
    moment of a destructive act, which names the count and offers the two tiers. What the
    filesystem says is still asked, because it is a fact and not a preference.

    Blocking: it reads the disk, so it runs off the event loop with the rest of these.
    """
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
    """Every folder a path implies, outermost first: a/b/c -> [a, a/b, a/b/c].

    A file at `a/b/c/clip.mp4` needs all three to exist, because each of them is somewhere a
    permission can be attached and somewhere the tree has to render a row.
    """
    segments = check_rel_path(rel_path).split("/")
    return ["/".join(segments[: index + 1]) for index in range(len(segments))]


# --- statements -----------------------------------------------------------------------------

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

#: The files that have a place under this root, before the root goes.
_LOCATED_UNDER_ROOT = "SELECT DISTINCT asset_id FROM asset_locations WHERE root_id = ?"
#: The moment a file's last place went. Only the ones left with none: a file that also sits in
#: another root still has a place and is not stranded at all.
_STRAND = """
UPDATE assets SET stranded_at = ?
 WHERE id IN (SELECT value FROM json_each(?))
   AND NOT EXISTS (SELECT 1 FROM asset_locations l WHERE l.asset_id = assets.id)
"""

# Hiding a library, for one user. An upsert because the state lives in a row of its own and most
# of them do not exist: nothing writes one until somebody hides the library. `hidden_at` is cleared
# on the way out so it always means "since when".
_SET_ROOT_HIDDEN = """
INSERT INTO root_user_state (root_id, user_id, hidden, hidden_at, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(root_id, user_id) DO UPDATE SET
    hidden     = excluded.hidden,
    hidden_at  = excluded.hidden_at,
    updated_at = excluded.updated_at
"""

_HIDDEN_ROOTS = "SELECT root_id FROM root_user_state WHERE user_id = ? AND hidden = 1"

# NULL leaves a column alone, which is what lets one statement serve a change of any one field.
# Written out rather than assembled from whichever fields the caller passed: a SET clause built at
# runtime is a query built from strings, and there is exactly one sanctioned way to do that here.
# Where a library now is. The name goes with it because a library is called what its directory is
# called: the two are one fact, and a statement that moved only one of them would let them drift
# apart.
_REPOINT_ROOT = "UPDATE library_roots SET name = ?, abs_path = ? WHERE id = ? RETURNING *"

# The folder row standing for the library itself carries the same name, so it moves with it. Every
# other folder is recorded relative to this one and is untouched by a library moving.
_RENAME_ROOT_FOLDER = "UPDATE folders SET name = ? WHERE root_id = ? AND parent_id IS NULL"


# `DO UPDATE SET rel_path = excluded.rel_path` writes nothing: it sets the path to the path it
# already has. It is there because DO NOTHING returns no row, and the folder that is already there
# is exactly what the caller needs. Nothing here touches who has hidden the folder: that lives in
# its own table, and a scan that walks a folder again must not un-hide it for anybody.
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

# Present only. A row already marked missing describes bytes that are not there, and a folder is
# recognised by what it still holds.
_LOCATIONS_IN_FOLDER = """
SELECT * FROM asset_locations
 WHERE folder_id = ? AND status = 'present'
"""

# --- moving a folder ---------------------------------------------------------------------------
#
# Five columns record where something sits inside a library, and a move rewrites all five in one
# transaction. They are written out, one statement each, rather than composed from a table name and
# a column name: there is no ORM here, so "no SQL is assembled from strings" is the whole of the
# injection control, and a statement nobody can find by grepping for the table it touches is a
# statement the next person reads past.
#
# Every one of them selects the same way: the row FOR the folder (`rel_path = :old`) or a row under
# it (`substr(...) = :prefix`), and rewrites by cutting the old path off the front and putting the
# new one on. `substr` is 1-based, so `:cut` is one past the old path and the folder's own row cuts
# down to the empty string, which lands it exactly on the new path.
_MOVE_FOLDERS = """
UPDATE folders
   SET rel_path = :new || substr(rel_path, :cut)
 WHERE root_id = :root
   AND (rel_path = :old OR substr(rel_path, 1, :prefix_length) = :prefix)
"""

# The moved folder's own name and the row it now hangs off. Its descendants keep both: moving
# `holidays` to `trips` does not rename `holidays/2024` and does not move it out of its parent.
_RENAME_FOLDER = "UPDATE folders SET name = :name, parent_id = :parent WHERE id = :id"

_MOVE_LOCATIONS = """
UPDATE asset_locations
   SET rel_path = :new || substr(rel_path, :cut)
 WHERE root_id = :root
   AND (rel_path = :old OR substr(rel_path, 1, :prefix_length) = :prefix)
"""

# A picture indexed out of an archive records the archive's own path as well as its own. Both are
# paths inside the library and both go stale together; missing this one leaves every picture in a
# moved gallery pointing at a zip that is not there.
_MOVE_ARCHIVE_LOCATIONS = """
UPDATE asset_locations
   SET archive_rel_path = :new || substr(archive_rel_path, :cut)
 WHERE root_id = :root
   AND archive_rel_path IS NOT NULL
   AND (archive_rel_path = :old OR substr(archive_rel_path, 1, :prefix_length) = :prefix)
"""

# A photo set made from an archive is found again by that archive's path. Left stale, the next scan
# does not recognise the gallery it already has and builds a second set beside it.
_MOVE_PHOTO_SETS = """
UPDATE photo_sets
   SET archive_rel_path = :new || substr(archive_rel_path, :cut)
 WHERE archive_root_id = :root
   AND archive_rel_path IS NOT NULL
   AND (archive_rel_path = :old OR substr(archive_rel_path, 1, :prefix_length) = :prefix)
"""

# A folder row made again (forgotten, then found by a walk) takes back the present files lying
# directly in it whose location lost its folder: an unchanged file is never rewritten to say so.
# The range's end is the prefix with its slash moved one character on, so it stays on the index.
_ADOPT_LOCATIONS = """
UPDATE asset_locations SET folder_id = ?
 WHERE root_id = ? AND folder_id IS NULL AND status = 'present'
   AND rel_path > ? AND rel_path < ?
   AND instr(substr(rel_path, ?), '/') = 0
"""

# The subtree of a folder, by path rather than by walking parents: every descendant's path starts
# with this folder's path and a separator. `substr` compares the prefix exactly, where LIKE would
# read a % or an _ in somebody's folder name as a wildcard and match more than the subtree.
_SET_FOLDER_SEEN_MTIME = "UPDATE folders SET seen_mtime = ? WHERE id = ?"

_FOLDERS_UNDER = """
SELECT * FROM folders
 WHERE root_id = ?
   AND substr(rel_path, 1, ?) = ?
 ORDER BY rel_path
"""

# Keyset paginated rather than an offset: the sweep reads a whole root while the scanner is
# writing to it, and OFFSET re-counts the rows it has already skipped on every page, so it gets
# slower the further it goes, and silently skips a row if one is inserted behind the cursor. The
# id is a ULID, so ordering by it is stable and a page always resumes exactly where the last ended.
#
# The prefix filters it to one folder's subtree, and the whole-root case is the same statement with
# an empty one: `substr(rel_path, 1, 0)` is `''`, which equals `''` for every row. That is worth
# the trick. A scan of one folder must sweep that folder and nothing else (sweeping wider marks
# every file outside it as missing, which is the whole library disappearing off the grid), and two
# statements, one narrow and one wide, is two chances to hand the sweep the wrong one.
_LOCATIONS_IN_ROOT_AFTER = """
SELECT * FROM asset_locations
 WHERE root_id = ? AND status = 'present' AND id > ?
   AND substr(rel_path, 1, ?) = ?
 ORDER BY id
 LIMIT ?
"""


class LibraryStore:
    """The roots and the folder tree. One per database.

    Held on the application rather than reached for through a global, for the same reason the
    content store beside it is: a global would have to hold a database handle, and a test wanting
    a different one would have to reach in and swap it out.
    """

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
        #: What else keeps a grant in use, and the sentence a refused give-back says for it.
        self._grant_users: list[tuple[str, GrantUser]] = []

    def _now(self) -> int:
        return int(self._clock())

    # --- granted folders ------------------------------------------------------------------
    #
    # A grant is what confines the folder picker.
    # Everything here is about WHERE SIFT MAY LOOK. Whether Sift may CHANGE anything in a folder
    # is the filesystem's answer, asked at the moment of the write: `check_folder_writable`.

    async def grants(self) -> list[Grant]:
        """Every folder handed to Sift, in the order a person reads them."""
        rows = await self._db.fetch_all(_GRANTS)
        return [grant_from_row(row) for row in rows]

    async def grant(self, abs_path: Path) -> Grant:
        """Record that somebody pointed the operating system's folder dialog at this folder.

        The path is resolved and checked exactly as a root's is, and for the same reasons: a stored
        path that reaches its directory by a different name compares as a different folder while
        behaving as the same one, and a grant over a directory Sift writes to would put the cache
        inside the area the picker offers as a library.

        An overlap is refused rather than merged. Granting a folder that already sits inside a grant
        adds nothing (the picker can already see it), and granting its parent would silently
        widen what an existing grant covers, which is the one thing a grant is supposed to make
        explicit.
        """
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
        """Take a folder back, unless something still uses it.

        Refused while a root lives inside it, and that is not tidiness. Revoking would leave the
        root indexed and served while the picker could no longer see the folder it came from, so
        the library would keep working, keep answering, and have no visible explanation anywhere for
        why its folder had vanished from the one screen that lists folders. Removing the library
        first is a decision somebody makes on purpose; this happening as a side effect is not. The
        same holds for every other use registered through `use_grants_for`.
        """
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

    # A grant follows what uses it. It is made when a folder is chosen for a use (a library, the
    # backup folder) and given back when the last use lets go, so the list of granted folders is
    # never a second list a person keeps by hand. A download destination needs no rule of its own:
    # it is a folder inside a library, so the library's grant covers it for as long as it exists.

    def use_grants_for(self, why: str, user: GrantUser) -> None:
        """Register a use of granted folders besides the libraries.

        `why` is the sentence shown when somebody tries to give back a folder it still uses, so it
        says what to change first. Registered once, at wiring.
        """
        self._grant_users.append((why, user))

    async def ensure_grant(self, abs_path: Path) -> Grant:
        """The grant that covers this folder, made for it when there is none.

        For a folder chosen for a use. A folder inside an existing grant is already covered, so
        that grant is the answer rather than a refusal. Every refusal `grant` makes otherwise
        stands: a missing folder, one of Sift's own, and a folder that would widen an existing grant.
        """
        resolved = await asyncio.to_thread(resolve_directory, abs_path)
        for existing in await self.grants():
            here = Path(existing.abs_path)
            if here == resolved or here in resolved.parents:
                return existing
        return await self.grant(resolved)

    async def release_unused_grants(self) -> list[Grant]:
        """Give back every granted folder nothing uses any more. Answers what was given back.

        Called when a use lets go: a library removed, the backup folder changed. A grant that a
        library or a registered use still overlaps stays, so calling this is never a risk.
        """
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

    # --- roots -----------------------------------------------------------------------------

    async def create_root(
        self,
        *,
        name: str,
        abs_path: Path,
        kind: RootKind = RootKind.LOCAL,
    ) -> Root:
        """Point Sift at a directory, with its folder row, or refuse to.

        The overlap check runs inside the write transaction and not before it. Read outside, two
        roots added at the same moment would each look at a library that did not contain the
        other, both would pass, and the rule would hold everywhere except the one case it exists
        for.
        """
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

        # The path is not logged. It is the one string in this feature that names a real person's
        # disk, and it says nothing about what happened that the id does not.
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
        """Which libraries this user has hidden.

        A set rather than a flag on `Root`, because a root row says nothing about who hid it: the
        same library is out of sight for one user and perfectly ordinary for the next. A screen
        listing roots reads this once and marks the ones it names.
        """
        rows = await self._db.fetch_all(_HIDDEN_ROOTS, (user_id,))
        return {str(row["root_id"]) for row in rows}

    async def set_root_hidden(self, user_id: str, root_id: str, *, hidden: bool) -> None:
        """Hide a library, or bring it back, for this user only.

        Unscoped: whether the caller may do this is settled at the route. Hiding a library conceals
        every folder and file under it on that user's screens, and nobody else's.
        """
        now = self._now()
        # Both in one transaction. The note is what makes the pictures this user already holds
        # unreachable; written after the hide had committed, there would be a moment in which a
        # whole concealed library was still readable out of the browser's own store.
        async with self._db.write() as connection:
            await connection.execute(
                _SET_ROOT_HIDDEN, (root_id, user_id, int(hidden), now if hidden else None, now)
            )
            announce(await bump_cache_stamp(connection, user_id), About.LIBRARY)

    async def repoint_root(self, root_id: str, abs_path: Path) -> Root | None:
        """The same library, at a new place on the disk. Every check adding one makes, again.

        Nothing under it moves: every file is recorded relative to its root, so re-pointing the
        one absolute path makes the whole library correct again, every share, concealment and
        attribution still on its folder. Removing and adding it back would recover the files by
        digest, lose the folders' grants and read every byte again.

        Refused for anything `create_root` would refuse. The overlap check skips this root's own
        row: the library is very often still recorded at a path that no longer exists.
        """
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
            # The row standing for the library itself is named after the directory, so it moves
            # with it. Everything under it is recorded relative to this one and does not change.
            await connection.execute(_RENAME_ROOT_FOLDER, (root.name, root_id))

        # The path is not logged, exactly as it is not logged when a root is added.
        log.info("library.root_repointed", root_id=root.id)
        return root

    async def delete_root(self, root_id: str, *, actor: Actor) -> bool:
        """Forget a root and everything indexed under it. Touches no file.

        The folders and locations go with it (the foreign keys cascade); the assets do not. An
        asset with no location left is content Sift still knows about and cannot currently see,
        which is the same state as a drive being unplugged, and it comes back the moment those
        bytes turn up anywhere it can read.
        """
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
                # Every file under it has left the library, for everyone who could see one.
                await announce_arrival(connection)
                # In the delete's own transaction, and the only record there will ever be: the row
                # goes, every folder under it cascades away with it, and afterwards nothing in the
                # database says this library was ever attached. The count of stranded files is in
                # the payload because that is the number somebody comes back for.
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
        # Written down when the last place went, so the promise made at the remove (add the
        # folder again and everything comes back) holds for a while against the Maintenance card
        # that removes stranded records. See `stranded_asset_ids`.
        if located:
            await self._write(_STRAND, (int(time.time()), json.dumps(located)))
        log.info("library.root_removed", root_id=root_id, stranded=len(located))
        # Its folder is given back once nothing else uses it. After the delete, which has already
        # happened: a failure here keeps a grant (the safe side) and must not report the removal
        # as failed.
        try:
            await self.release_unused_grants()
        except Exception:
            log.exception("library.grants_release_failed", root_id=root_id)
        return True

    async def folder_ids_in_root(self, root_id: str) -> list[str]:
        """Every folder id under a root, for a caller that has to clean up after them.

        Deleting a root cascades its folders away, and nothing cascades the permissions that name
        them: those grants carry no foreign key, because the id they hold could belong to any of
        several tables. So they have to be dropped deliberately, and this is what says which.
        """
        rows = await self._db.fetch_all(_FOLDER_IDS_IN_ROOT, (root_id,))
        return [str(row["id"]) for row in rows]

    # --- folders ---------------------------------------------------------------------------

    async def upsert_folder(self, root_id: str, rel_path: str) -> FolderRow:
        """The folder at this path, creating it and every folder above it if need be.

        A scan calls this for every directory it walks, so it has to be idempotent and it has to
        be safe to run twice at the same time: the conflict clause makes a folder that already
        exists come back rather than fail.
        """
        chain = _folder_chain(rel_path)
        async with self._db.write() as connection:
            parent = await self._require_root_folder(connection, root_id)
            for path in chain:
                parent = await self._insert_folder(
                    connection,
                    root_id=root_id,
                    parent_id=parent.id,
                    rel_path=path,
                    # Straight off the filesystem, and part of the path text that is indexed.
                    name=clean_stored_text(path.rsplit("/", 1)[-1]),
                )
            return parent

    async def root_folder(self, root_id: str) -> FolderRow | None:
        """The folder row standing for the root directory itself."""
        return await self.folder_at(root_id, ROOT_REL_PATH)

    async def folder_at(self, root_id: str, rel_path: str) -> FolderRow | None:
        """The folder at this path, or None. A read: it never creates one.

        Its own method beside `upsert_folder` because the two answer different questions at very
        different costs. Upserting opens a write transaction, which is right when a scan is taking a
        file in and wrong when a pass is only asking what a directory already is, and a pass over
        a library asks that thousands of times.
        """
        row = await self._db.fetch_one(_FOLDER_AT, (root_id, rel_path))
        return None if row is None else folder_from_row(row)

    async def get_folder(self, folder_id: str) -> FolderRow | None:
        """A folder row, with no permission check (see the module docstring).

        A caller acting for a person asks the access layer instead. This is for the callers that
        are acting for nobody: a scan walking a root, and the downloader working out where a file
        it was told to fetch should land.
        """
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
        """Every folder row UNDER a path, by path rather than by walking parents.

        Its own method beside `folders_under`, which needs a `FolderRow` to start from, and the
        whole library has no such row to be under. The empty path is every folder in the root, by
        the same `substr(x, 1, 0) = ''` the sweep uses, so a scan of a library and a scan of one
        folder inside it ask this the same way and cannot be handed the wrong shape of question.

        **The folder named by `under` is not in the answer, except when it is the library root.** A
        subtree is found by prefix and the prefix is the path plus a slash, so `sets` never matches
        `sets/`. But the root's path is the empty string, which every path starts with, its own
        included. Every caller has to decide what it wants for that one row: the two scan callers
        drop it by path, and the folder delete puts it back by id, otherwise it would remove a
        folder from the inside out and leave it standing.
        """
        prefix = subtree_prefix(under)
        rows = await self._db.fetch_all(_FOLDERS_UNDER, (root_id, len(prefix), prefix))
        return [folder_from_row(row) for row in rows]

    async def locations_in_folder(self, folder_id: str) -> list[Location]:
        """What Sift believes is directly inside one folder, and still there.

        Directly inside, never underneath: a folder is recognised by what it holds, and counting a
        subfolder's files would let a parent be mistaken for one of its own children.
        """
        if not is_id(folder_id):
            return []
        rows = await self._db.fetch_all(_LOCATIONS_IN_FOLDER, (folder_id,))
        return [location_from_row(row) for row in rows]

    async def move_folder(
        self, folder: FolderRow, new_rel_path: str, *, actor: Actor
    ) -> FolderRow | None:
        """The same folder, somewhere else in its library. The one place a stored path is rewritten.

        THE FOLDER KEEPS ITS ID: a share, a restrict, a concealment and an attribution rule are
        written on the folder, not its files, so a folder deleted and re-created loses them all.

        The five columns that record where something sits move together, listed out because a
        column this misses is a row quietly pointing at a path that no longer exists
        (`tests/gates/test_one_stored_path.py` holds the list); `scan_rejections` belongs to its
        feature and is moved by the same act, a line later. One transaction. Refused where the
        destination is already taken: then the two are not the same folder, and the caller treats
        it as new.
        """
        new_rel_path = check_rel_path(new_rel_path)
        old = folder.rel_path
        if old == ROOT_REL_PATH:
            # The row standing for the library itself. Its path is the empty string and it moves
            # only when the whole library does, which is a different operation with its own checks.
            return None

        taken = await self._db.fetch_one(_FOLDER_AT, (folder.root_id, new_rel_path))
        if taken is not None:
            return None

        # What will hold it. A folder's place in the tree is recorded twice (as a path and as a
        # parent), and a move that rewrote only the path would leave a folder sitting under a
        # heading it is not inside. The row above has to exist already; the caller makes the chain
        # before asking for the move, because inventing a missing parent here would be this method
        # quietly deciding where somebody's folder went.
        above = new_rel_path.rsplit("/", 1)[0] if "/" in new_rel_path else ROOT_REL_PATH
        holder = await self._db.fetch_one(_FOLDER_AT, (folder.root_id, above))
        if holder is None:
            return None

        # `substr` is 1-based, so the cut is one past the old path: the row for the folder itself
        # takes `substr(old, len(old) + 1)`, which is the empty string, and lands exactly on the
        # new path. Every descendant keeps the part after it.
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
            # The path is the only record of where a folder was, and this rewrites it in five
            # tables together, so without an event there is nothing anywhere that says the folder
            # somebody is looking for used to be somewhere else. The old path is in the payload;
            # the new one is the snapshot, because that is what the folder is called now.
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
        """Remember what a folder's directory looked like at the moment it was walked.

        Written by the scan, read by the catch-up at start. `None` is stored rather than skipped
        when the directory could not be stat'd: a folder Sift could not look at is one it cannot
        rule out later, and leaving the old value would say the opposite.
        """
        if not is_id(folder_id):
            return
        await self._write(_SET_FOLDER_SEEN_MTIME, (mtime, folder_id))

    async def folders_in_root(self, root_id: str) -> list[FolderRow]:
        """Every folder row in one library, in one answer.

        For the catch-up at start, which asks about each of them and needs the whole set in front
        of it. `folders_in_subtree` with the empty path is the same rows; this exists so the caller
        does not have to know that the empty string is how a root is spelled.
        """
        return await self.folders_in_subtree(root_id, ROOT_REL_PATH)

    async def remove_folder(self, folder_id: str) -> bool:
        """Forget one folder and everything under it. Touches no file.

        Its descendants go with it (`parent_id` cascades), and so do the rows of every feature
        that hangs off a folder. The locations do not: `asset_locations.folder_id` is `SET NULL`,
        so a file whose folder row has gone is still a file Sift knows about, which is right when
        the folder came back under another name and this was called too early.

        **The grants that name it are NOT dropped here**, deliberately, and the caller does it
        first: the same order and the same reason as removing a whole library: a grant left
        behind names an id, and an id is not a promise never to be reused.
        """
        if not is_id(folder_id):
            return False
        async with self._db.write() as connection:
            rows = list(await connection.execute_fetchall(_DELETE_FOLDER, (folder_id,)))
            if rows:
                # The folder and every file under it have left what anybody could be drawing.
                await announce_arrival(connection)
        if not rows:
            return False
        log.info("library.folder_removed", folder_id=folder_id)
        return True

    # --- the sweep -------------------------------------------------------------------------

    async def iter_locations_in_root(
        self, root_id: str, *, under: str = ROOT_REL_PATH, batch: int = 500
    ) -> AsyncIterator[Location]:
        """Every location Sift currently believes is present, a batch at a time.

        A scan ends by marking what it did not see as missing, and a million rows are not read
        into memory for that: this yields them in id order, one batch held at a time. `under`
        keeps it to one folder's subtree, exactly the files a scan of that folder marks missing
        when unseen; the default is the root's own folder, which everything is under. Not a
        snapshot: a row inserted behind the cursor is a file the scanner just saw.
        """
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

    # --- internals -------------------------------------------------------------------------

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
        """One write to the folders given to Sift, and every admin told once it lands.

        Every admin, because giving Sift a folder or taking one back is an admin's act, and the
        list of them is drawn for admins alone.
        """
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            return list(await connection.execute_fetchall(sql, params))


def subtree_prefix(rel_path: str) -> str:
    """What every path inside a folder starts with.

    The root's own folder is the empty path and everything in the root is inside it, so its prefix
    is the empty string, which every path starts with, including its own.
    """
    return "" if rel_path == ROOT_REL_PATH else rel_path + "/"


# --- what a piece of work is about ---------------------------------------------------------------
#
# Lives here, in the module that owns these tables, because nothing outside the kernel may write
# SQL against them: a query that reads an asset belongs beside the access rules for assets, not
# in the screen that happens to want one. The jobs dashboard is the caller: a queue row carries an
# identifier, and twenty files being read is twenty identical rows unless something turns those
# identifiers back into names.

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
    """The filename each asset is known by, for the ids that still exist.

    The name on disk where there is one, and the name the file arrived under otherwise: an asset
    whose only copy has gone missing still has a name worth showing. An id with no row is simply
    absent from the result: a file deleted since its job was queued has no name to give, and the
    row that wanted it says what the work was instead.
    """
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
