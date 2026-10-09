# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the faces in a folder say, for the folder reader, as counts only, across a seam.

Which folder holds which file is asked of the kernel, never of these tables, and groups set aside
count for nothing here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Protocol

from sift.kernel.attribution import FolderFaces, FolderStamp
from sift.kernel.content import TreeReads
from sift.kernel.db import Connection, Database, in_clause
from sift.kernel.jobs import JobQueue
from sift.kernel.log import get_logger
from sift.kernel.paging import MAX_PAGE_SIZE
from sift.slices.faces.jobs import ask_for_rematching
from sift.slices.faces.models import Attribution
from sift.slices.faces.settings import ENABLED_KEY
from sift.slices.faces.store import NAMES_THE_FILE, Store, now_ms

log = get_logger(__name__)

#: `store.NAMES_THE_FILE` as `_CONTRADICTS` and `_NAMED_OVER` bind it.
_NAMES_THE_FILE = tuple(one.value for one in NAMES_THE_FILE)
if len(_NAMES_THE_FILE) != 2:  # pragma: no cover (an edit to the rule without this statement)
    raise RuntimeError("two statements bind one placeholder per attribution that names a file")

#: Why a group is proposed as somebody, as `face_pile_proposals.reason` spells it.
FOLDER_REASON = "folder"

#: File ids one statement binds: SQLite caps placeholders, and a folder can hold many files.
_CHUNK = 500

# How far a pass got with each file: `no_faces` (looked, nobody) is the opposite of no row.
_LOOKED_AT = "SELECT asset_id, status FROM face_scans WHERE asset_id IN (?*)"

# The open groups over these files, by FILE, so one long clip does not outweigh many photographs.
# `CROSS JOIN` fixes the join order, faces first by file, which is the whole cost.
_PILES_OVER = """
SELECT t.pile_id AS pile_id, t.asset_id AS asset_id, MIN(t.id) AS portrait
  FROM face_tracks t
  CROSS JOIN face_piles p ON p.id = t.pile_id AND p.status = 'open'
 WHERE t.person_id IS NULL AND t.asset_id IN (?*)
 GROUP BY t.pile_id, t.asset_id
"""

# The people already named in these files: only a face that names its file (`NAMES_THE_FILE`).
_NAMED_OVER = """
SELECT DISTINCT t.person_id AS person_id, t.asset_id AS asset_id
  FROM face_tracks t
 WHERE t.person_id IS NOT NULL AND t.asset_id IN (?*) AND t.attribution IN (?, ?)
"""

# Files with a face naming somebody else, or refused as this person, and none of hers. A file with
# no face, or only unnamed or asked faces, contradicts nothing.
_CONTRADICTS = """
SELECT DISTINCT t.asset_id AS asset_id
  FROM face_tracks t
 WHERE t.asset_id IN (?*)
   AND ((t.person_id IS NOT NULL AND t.person_id IS NOT ? AND t.attribution IN (?, ?))
        OR EXISTS (SELECT 1 FROM face_rejections r
                    WHERE r.track_id = t.id AND r.person_id = ?))
   AND NOT EXISTS (SELECT 1 FROM face_tracks o
                    WHERE o.asset_id = t.asset_id AND o.person_id IS ?)
"""

# The groups of these files' faces: a file outside a folder's leading group dissents from it.
_GROUPS_OF_FILES = "SELECT DISTINCT asset_id, pile_id FROM face_tracks WHERE asset_id IN (?*)"

# The whole-library read per file; the newest moment counts each face's group's birth too, so a
# rebuilt group moves its folders' stamps.
_STAMPS_BY_ASSET = """
SELECT t.asset_id AS asset_id, COUNT(*) AS tracks,
       COALESCE(MAX(MAX(COALESCE(t.attributed_at, t.created_at), COALESCE(p.created_at, 0))), 0)
         AS latest
  FROM face_tracks t
  LEFT JOIN face_piles p ON p.id = t.pile_id
 GROUP BY t.asset_id
"""

_SCANNED_ASSETS = "SELECT asset_id FROM face_scans"

# Naming every unclaimed face in a group, on a caller's connection.
_NAME_GROUP = """
UPDATE face_tracks
   SET person_id = ?, confidence = 1.0, attribution = ?, attributed_at = ?
 WHERE pile_id = ? AND person_id IS NULL
"""

# Which faces the statement above is about to change, read just before; makes naming reversible.
_UNCLAIMED_IN_GROUP = "SELECT id FROM face_tracks WHERE pile_id = ? AND person_id IS NULL"

# Putting one back by id; `attributed_at` goes with the name.
_UNNAME_FACE = """
UPDATE face_tracks
   SET person_id = NULL, confidence = 0.0, attribution = NULL, attributed_at = NULL
 WHERE id = ?
"""


async def _found_now(connection: Connection, track_ids: Sequence[str]) -> list[str]:
    """These faces by the ids they go by now (`Store.live_ids`), on the caller's connection."""
    wanted = list(dict.fromkeys(track_ids))
    live = {one: one for one in wanted}
    for start in range(0, len(wanted), MAX_PAGE_SIZE):
        query, bound = in_clause(
            "SELECT track_id, successor_id FROM face_successors WHERE track_id IN (?*)",
            wanted[start : start + MAX_PAGE_SIZE],
        )
        for row in await connection.execute_fetchall(query, bound):
            live[str(row["track_id"])] = str(row["successor_id"])
    return list(dict.fromkeys(live[one] for one in wanted))


class Teacher(Protocol):
    """The two halves of turning named faces into references, as the face service offers them."""

    async def teach(self, track_ids: Sequence[str], person_id: str) -> int: ...
    async def unteach(self, track_ids: Sequence[str], person_id: str) -> int: ...


class FaceEvidence:
    """The face feature as a folder reader asks about it: reads, naming a group on the caller's
    connection, teaching afterwards, and keeping proposals."""

    def __init__(
        self,
        database: Database,
        *,
        preferences: object,
        store: Store,
        teacher: Teacher,
        queue: JobQueue,
    ) -> None:
        self._db = database
        self._tree = TreeReads(database)
        self._preferences = preferences
        self._store = store
        self._teacher = teacher
        self._queue = queue

    async def looking(self) -> bool:
        """Whether recognition is switched on; nothing found yet is not nothing ever found."""
        return bool(await self._preferences.get_app(ENABLED_KEY))  # type: ignore[attr-defined]

    async def stamps(self) -> dict[str, FolderStamp]:
        """A number per folder that moves when its faces do, folded where the folder map is."""
        per_asset = {
            str(row["asset_id"]): (int(row["tracks"]), int(row["latest"]))
            for row in await self._db.sweep_all(_STAMPS_BY_ASSET, what="faces per file")
        }
        scanned = {
            str(row["asset_id"])
            for row in await self._db.sweep_all(_SCANNED_ASSETS, what="files looked at")
        }
        totals = await self._tree.totals_by_folder(per_asset, scanned)
        return {
            folder_id: FolderStamp(
                tracks=total.summed, looked_at=total.counted, latest=total.newest
            )
            for folder_id, total in totals.items()
        }

    async def faces_in(self, folder_id: str) -> FolderFaces:
        return (await self.faces_in_many([folder_id]))[folder_id]

    async def faces_in_many(self, folder_ids: Sequence[str]) -> dict[str, FolderFaces]:
        """`faces_in` for each of these folders, the faces read once for all of their files."""
        under = await self._tree.assets_under_many(folder_ids)
        every = list(dict.fromkeys(asset for assets in under.values() for asset in assets))
        status: dict[str, str] = {}
        piles_of: dict[str, dict[str, str]] = {}
        named_of: dict[str, set[str]] = {}
        for batch in _batched(every):
            query, bound = in_clause(_LOOKED_AT, batch)
            for row in await self._db.fetch_all(query, bound):
                status[str(row["asset_id"])] = str(row["status"])
            query, bound = in_clause(_PILES_OVER, batch)
            for row in await self._db.fetch_all(query, bound):
                piles_of.setdefault(str(row["asset_id"]), {})[str(row["pile_id"])] = str(
                    row["portrait"]
                )
            query, bound = in_clause(_NAMED_OVER, batch)
            for row in await self._db.fetch_all(query, [*bound, *_NAMES_THE_FILE]):
                named_of.setdefault(str(row["asset_id"]), set()).add(str(row["person_id"]))

        found = {
            one: _folder_faces(assets, status, piles_of, named_of) for one, assets in under.items()
        }
        led = list(
            dict.fromkeys(
                asset for one, assets in under.items() if found[one].piles for asset in assets
            )
        )
        groups_of: dict[str, set[str | None]] = {}
        for batch in _batched(led):
            query, bound = in_clause(_GROUPS_OF_FILES, batch)
            for row in await self._db.fetch_all(query, bound):
                pile = row["pile_id"]
                groups_of.setdefault(str(row["asset_id"]), set()).add(
                    None if pile is None else str(pile)
                )
        for one, faces in found.items():
            if not faces.piles:
                continue
            leader = max(faces.piles.items(), key=lambda pair: (pair[1], pair[0]))[0]
            dissenting = sorted(
                {
                    asset
                    for asset in under[one]
                    if asset in groups_of and leader not in groups_of[asset]
                }
            )
            found[one] = replace(faces, dissenting=tuple(dissenting))
        return found

    async def contradicting(self, person_id: str, asset_ids: Sequence[str]) -> set[str]:
        """Of these files, the ones whose faces say they are not this person's (`_CONTRADICTS`).

        A silent attribution must be narrower than an approved one.
        """
        if not asset_ids:
            return set()
        found: set[str] = set()
        for batch in _batched(list(asset_ids)):
            query, bound = in_clause(_CONTRADICTS, batch)
            rows = await self._db.fetch_all(
                query, [*bound, person_id, *_NAMES_THE_FILE, person_id, person_id]
            )
            found.update(str(row["asset_id"]) for row in rows)
        return found

    async def name_group(self, connection: Connection, group_id: str, person_id: str) -> int:
        """Say who a whole group is. Returns how many appearances took the name."""
        return len(await self.name_group_recording(connection, group_id, person_id))

    async def name_group_recording(
        self, connection: Connection, group_id: str, person_id: str
    ) -> list[str]:
        """The same write, saying which appearances took the name; only unclaimed faces do."""
        found = await (await connection.execute(_UNCLAIMED_IN_GROUP, (group_id,))).fetchall()
        named = [str(row["id"]) for row in found]
        if not named:
            return []
        await connection.execute(
            _NAME_GROUP,
            # Milliseconds, as every other writer of the column uses.
            (person_id, Attribution.CONFIRMED.value, now_ms(), group_id),
        )
        log.info("faces.group.named", group_id=group_id, faces=len(named))
        return named

    async def unname_faces(self, connection: Connection, track_ids: Sequence[str]) -> int:
        """Put these appearances back to unnamed, by id. Returns how many moved."""
        moved = 0
        for track_id in await _found_now(connection, track_ids):
            cursor = await connection.execute(_UNNAME_FACE, (track_id,))
            moved += int(cursor.rowcount or 0)
        if moved:
            log.info("faces.group.unnamed", faces=moved)
        return moved

    async def teach(self, track_ids: Sequence[str], person_id: str) -> int:
        """Let faces a folder's answer named teach Sift who this person is. Returns how many did.

        Through the service's `confirm`, after the naming has landed, then a re-match.
        """
        if not track_ids:
            return 0
        taught = await self._teacher.teach(track_ids, person_id)
        if taught:
            await ask_for_rematching(self._queue)
            log.info("faces.group.taught", person_id=person_id, faces=taught)
        return taught

    async def unteach(self, track_ids: Sequence[str], person_id: str) -> int:
        """Take back what `teach` filed for these faces. Returns how many lost something."""
        if not track_ids:
            return 0
        taken = await self._teacher.unteach(track_ids, person_id)
        if taken:
            await ask_for_rematching(self._queue)
            log.info("faces.group.untaught", person_id=person_id, faces=taken)
        return taken

    async def propose_group(
        self,
        group_id: str,
        person_id: str,
        *,
        folder_id: str,
        files: int,
        of: int,
    ) -> bool:
        """Keep "this group may be this person" for the review list. True when it is standing."""
        return await self._store.propose_pile(
            group_id,
            person_id,
            reason=FOLDER_REASON,
            folder_id=folder_id,
            files=files,
            of_files=of,
        )

    async def withdraw_proposals(self, folder_id: str, person_id: str) -> int:
        """This folder no longer has a main group for this person: take back what is pending."""
        return await self._store.withdraw_pile_proposals(folder_id, person_id)


def _folder_faces(
    assets: Sequence[str],
    status: dict[str, str],
    piles_of: dict[str, dict[str, str]],
    named_of: dict[str, set[str]],
) -> FolderFaces:
    """One folder's counts out of what was read for every folder's files."""
    if not assets:
        return FolderFaces()
    looked_at = with_faces = 0
    piles: dict[str, int] = {}
    portraits: dict[str, str] = {}
    named: dict[str, int] = {}
    for asset in assets:
        if asset in status:
            looked_at += 1
            if status[asset] != "no_faces":
                with_faces += 1
        for pile_id, portrait in piles_of.get(asset, {}).items():
            piles[pile_id] = piles.get(pile_id, 0) + 1
            portraits[pile_id] = min(portraits.get(pile_id, portrait), portrait)
        for person_id in named_of.get(asset, ()):
            named[person_id] = named.get(person_id, 0) + 1
    return FolderFaces(
        looked_at=looked_at, with_faces=with_faces, piles=piles, named=named, portraits=portraits
    )


def _batched(ids: Sequence[str]) -> list[list[str]]:
    return [list(ids[start : start + _CHUNK]) for start in range(0, len(ids), _CHUNK)]
