# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the faces in a folder say, for whoever is reading folder names for people.

The other side of a seam. A feature that reads a folder tree for names needs corroboration and may
not import this one, so it asks a question shaped like counts and gets counts back: no pile, no
embedding, no model, and nothing it could use to do recognition's job itself.

**Which folder holds which file is asked of the kernel, never of these tables.** Everything here
takes a list of file ids and answers about faces; the mapping from a folder to those ids comes from
the one place allowed to name the tables that decide who may see what. It is a real cost (the
whole-library stamp reads the mapping and joins it in memory), and it is what keeps a feature that
knows nothing about permissions from writing a query that walks past them.

Groups somebody has set aside are excluded from every answer here. That is not a filter applied by
the caller and it must not become one: somebody has already said those faces are not worth naming,
and work that comes back after being dismissed is worse than work that was never offered.
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

#: `store.NAMES_THE_FILE` as the values `_CONTRADICTS` and `_NAMED_OVER` bind: one `?` for each,
#: written out.
_NAMES_THE_FILE = tuple(one.value for one in NAMES_THE_FILE)
if len(_NAMES_THE_FILE) != 2:  # pragma: no cover (an edit to the rule without this statement)
    raise RuntimeError("two statements bind one placeholder per attribution that names a file")

#: Why a group is proposed as somebody, as `face_pile_proposals.reason` spells it.
FOLDER_REASON = "folder"

#: How many file ids one statement binds at a time.
#:
#: SQLite refuses a statement past a fixed number of placeholders, and a folder can hold a hundred
#: thousand files. Chunked rather than assembled, because the alternative (one statement built to
#: fit) is a statement whose text depends on how much media somebody has.
_CHUNK = 500

# How far a pass has got with each file, and whether it found anybody. `no_faces` is the status a
# pass writes when it looked and found nobody, which is what makes the second number readable: a
# file with that status has been looked at and holds no face, and a file with no row at all has not
# been looked at. Those are opposite answers and the whole ladder turns on telling them apart.
_LOOKED_AT = "SELECT asset_id, status FROM face_scans WHERE asset_id IN (?*)"

# The open groups over these files, each against how many of the FILES it appears in.
#
# Files rather than faces, deliberately. A video where one person is tracked through forty frames is
# one file about one person, and counting the frames would make a single long clip outweigh thirty
# photographs of somebody else.
#
# `p.status = 'open'` is the exclusion, and the join is inner on purpose: a face in a pile somebody
# set aside contributes to nothing here, and neither does one whose pile has gone.
#
# `CROSS JOIN` is SQLite's way of fixing the join order, and the order is the whole cost. Written as
# a plain JOIN the planner puts the piles first (every open group in the library, probing each
# one's faces) and filters by file afterwards, so every chunk of five hundred files walks the
# whole of the unnamed faces: several times slower than faces first, found by file through their
# index with each group then probed by its key, and multiplied by every chunk of every folder on the
# Folders page. The rows are identical either way; only the walk differs.
_PILES_OVER = """
SELECT t.pile_id AS pile_id, t.asset_id AS asset_id, MIN(t.id) AS portrait
  FROM face_tracks t
  CROSS JOIN face_piles p ON p.id = t.pile_id AND p.status = 'open'
 WHERE t.person_id IS NULL AND t.asset_id IN (?*)
 GROUP BY t.pile_id, t.asset_id
"""

# The people already recognized in these files, each against how many of them they are in.
#
# Only a face that NAMES its file counts (`store.NAMES_THE_FILE`, bound after the files): what the
# folder reader asks is who somebody has already named here, and it gives a whole folder away
# without asking on the answer. A face Sift is only asking about puts nobody on its own file, so it
# may not put anybody on a folder.
_NAMED_OVER = """
SELECT DISTINCT t.person_id AS person_id, t.asset_id AS asset_id
  FROM face_tracks t
 WHERE t.person_id IS NOT NULL AND t.asset_id IN (?*) AND t.attribution IN (?, ?)
"""

# Which of these files carry a face that says it is somebody ELSE, and none of the person's own.
#
# The test a silent attribution has to pass. A file with no face in it at all is absent from this,
# and that is the point: it contradicts nothing, so a folder full of body-only clips still takes the
# folder's name. What this finds is the other case: a file whose faces are other people's,
# sitting inside a folder named after somebody. That is a file which is in the folder for a reason
# that has nothing to do with the folder's name.
#
# **A face says it is somebody else in exactly two ways**: it NAMES another person (by the one
# rule for which attributions name a file (`store.NAMES_THE_FILE`: confirmed, or recognized above
# the attach line), bound in rather than spelt here), or somebody refused it as THIS person.
#
# An unnamed face is not somebody else's: it is a face nobody has recognized yet, which in a folder
# filed under her is most often her own face in a picture Sift has no reference close enough to.
# Counting it would hold back exactly her own face-bearing files. A question standing about the
# face (`suggested`) is not a name either, for the same reason a question writes no name onto the
# file.
#
# A face that merely carries HER, in any state, still clears the file: that half is unchanged.
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

# The groups the faces of these files are in: a file with a face and none in a folder's leading
# group dissents from it.
_GROUPS_OF_FILES = "SELECT DISTINCT asset_id, pile_id FROM face_tracks WHERE asset_id IN (?*)"

# The cheap whole-library read, per FILE. Turned into a per-folder number in memory against the
# mapping the kernel supplies. See the module docstring.
#
# The newest moment counts the birth of each face's GROUP as well as the face's own. A full
# regroup gives a rebuilt group a new id without moving a single face, and the folder reader
# names a group by its id: without the group's birth here a folder's stamp would stand still,
# the reader would never look at it again, and the proposal the rebuild took with the old group
# would never be made for the new one. A group the rebuild kept is the same row with the same birth, so
# its folders stay where they were.
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
#
# Unclaimed only: a face already attributed to somebody was decided elsewhere, and a folder name is
# not evidence enough to overrule it.
_NAME_GROUP = """
UPDATE face_tracks
   SET person_id = ?, confidence = 1.0, attribution = ?, attributed_at = ?
 WHERE pile_id = ? AND person_id IS NULL
"""

# Which faces the statement above is about to change, read on the same connection a moment before
# it runs. The pair is what makes naming a group reversible; see `name_group_recording`.
_UNCLAIMED_IN_GROUP = "SELECT id FROM face_tracks WHERE pile_id = ? AND person_id IS NULL"

# And putting one back, by id. `attributed_at` goes with the name: a face with no person on it and
# a moment it was attributed would be a row describing a decision that has been taken back.
_UNNAME_FACE = """
UPDATE face_tracks
   SET person_id = NULL, confidence = 0.0, attribution = NULL, attributed_at = NULL
 WHERE id = ?
"""


async def _found_now(connection: Connection, track_ids: Sequence[str]) -> list[str]:
    """These faces by the ids they go by now, read on the caller's connection.

    The same answer as `Store.live_ids`, asked inside the transaction the undo is already holding: a
    file rescanned since the folder's answer found its faces again under new ids, and unnaming the
    old ones would leave the face named.
    """
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
    """The two halves of turning named faces into references, as the face service offers them.

    A protocol rather than the service itself so this module does not import the service (which
    imports this slice's package, which imports this module), and so its tests can stand in for it.
    """

    async def teach(self, track_ids: Sequence[str], person_id: str) -> int: ...
    async def unteach(self, track_ids: Sequence[str], person_id: str) -> int: ...


class FaceEvidence:
    """The face feature as a folder reader asks about it.

    Reads, and three kinds of write. `name_group` takes the caller's connection because confirming
    a folder names the group AND attributes the files in one action, and half of that landing is
    worse than neither. `teach` / `unteach` run afterwards on their own connections, because they
    decode pictures. `propose_group` / `withdraw_proposals` keep what the folder reader concluded
    about a group, for the review list to ask.

    `teacher` is the face service and `queue` is where a re-match is asked for, the same request
    every other naming press makes. Required, all three: an instance built without them would take
    a name and teach nothing.
    """

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
        """Whether recognition is switched on at all.

        Asked rather than inferred from empty answers, because "nothing found yet" and "nothing
        will ever be found" lead to opposite decisions: the first is a folder worth waiting for and
        the second is a folder that has to be judged on its name alone.
        """
        return bool(await self._preferences.get_app(ENABLED_KEY))  # type: ignore[attr-defined]

    async def stamps(self) -> dict[str, FolderStamp]:
        """A number per folder that moves when its faces do.

        Two reads, and the adding up is done by the database rather than here. The boundary is why
        the numbers make a round trip at all: this feature may not ask which folder a file is in, so
        it counts per file and hands those counts to the kernel, which knows where each file sits
        and nothing about what the numbers mean.

        The file-to-folder map, hundreds of thousands of rows on a large library, is never read out
        into Python: the fold happens where the map already is, which is faster and a fraction of
        the memory.
        """
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
        # Only where a folder has a leading group is anything dissenting from it.
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
        """Of these files, the ones whose faces say they are not this person's.

        A file qualifies only by holding a face that says it is somebody else (NAMED as another
        person, or refused as this one) and no face of theirs. Three things are deliberately NOT
        in the answer: a file nothing has looked at yet, a file that was looked at and holds no face
        at all, and a file whose faces nobody has put a name to. None of those contradicts
        anything: the first two are what keeps a folder of body-only clips working, and the third is
        what keeps her own unrecognized pictures in her folder (see `_CONTRADICTS`).

        This exists because attributing a folder to somebody whose face already runs through it is
        allowed to happen without asking, and a silent write has to be narrower than one somebody
        approved. A subfolder of other people's videos sitting inside a folder named after her is
        the case: it took her name, in her library, with nothing on any screen saying so.
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
        """The same write, saying WHICH appearances took the name rather than how many.

        The narrow half of what naming faces one at a time does: the person goes on the row and the
        decision is marked as somebody's rather than the arithmetic's. What it deliberately does
        NOT do is cut a portrait or rebuild the person's reference gallery: both decode media,
        and this runs with the single write connection held.

        Which ones is what makes it reversible. Only unclaimed faces take the name, so a group of
        thirty where six were already somebody's names twenty-four, and putting that back by
        clearing everything in the group that carries the person would take the six with it, which
        were decided somewhere else and are not this decision's to touch.
        """
        found = await (await connection.execute(_UNCLAIMED_IN_GROUP, (group_id,))).fetchall()
        named = [str(row["id"]) for row in found]
        if not named:
            return []
        await connection.execute(
            _NAME_GROUP,
            # Milliseconds, the unit every other writer of the column uses (`store.attribute`); in
            # seconds a group named here would sort as decided in 1970 on every read ordered by it.
            (person_id, Attribution.CONFIRMED.value, now_ms(), group_id),
        )
        log.info("faces.group.named", group_id=group_id, faces=len(named))
        return named

    async def unname_faces(self, connection: Connection, track_ids: Sequence[str]) -> int:
        """Put these appearances back to unnamed. Returns how many moved.

        Named by id rather than by group, so an undo puts back exactly the appearances its own
        decision named. It does not restore what each one said BEFORE: there was nothing to
        restore, because only unclaimed faces are ever named this way.
        """
        moved = 0
        for track_id in await _found_now(connection, track_ids):
            cursor = await connection.execute(_UNNAME_FACE, (track_id,))
            moved += int(cursor.rowcount or 0)
        if moved:
            log.info("faces.group.unnamed", faces=moved)
        return moved

    async def teach(self, track_ids: Sequence[str], person_id: str) -> int:
        """Let faces a folder's answer named teach Sift who this person is. Returns how many did.

        **The half `name_group_recording` leaves out, done afterwards on its own connections.**
        Naming takes the single write connection and cannot decode a picture while holding it; this
        runs once that transaction has landed and goes through the ONE path by which a face becomes
        a reference: the face service's own `confirm`, for each face: its pictures filed as the
        person's references, the decision remembered against the face's description so a rescan
        of the file puts the name back as a decision, and the People on each file brought into
        line. Then a re-match is asked for, as after every other naming press, so the rest of the
        library is compared with the gallery that just grew.

        Without it a Yes on the Folders card would name the group and nothing more: no reference, so
        the person is recognized by nothing new; no remembered decision, so a rescan of any of those
        files drops the name; and no re-match.

        Only faces still carrying THIS person are taught. See `FaceService.teach`. Zero on an
        install with recognition switched off, where there is nothing to teach.
        """
        if not track_ids:
            return 0
        taught = await self._teacher.teach(track_ids, person_id)
        if taught:
            await ask_for_rematching(self._queue)
            log.info("faces.group.taught", person_id=person_id, faces=taught)
        return taught

    async def unteach(self, track_ids: Sequence[str], person_id: str) -> int:
        """Take back what `teach` filed for these faces. Returns how many faces it was taken from.

        What an undo of the folder answer runs once its own transaction has put the faces back to
        unnamed: their references and their remembered decision go too, or the person would go on
        being recognized by pictures of a decision somebody took back, and a rescan would name the
        faces again.
        """
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
        """Keep "this group may be this person" for the review list. True when it is standing.

        The folder reader's conclusion, handed across because it may not be worked out here: the
        folder is filed under this person, and this group makes up most of the folder's files with
        a face. `files` is the group's files in the folder and `of` the folder's files with a face:
        the reader's own evidence, kept with the row. An answered proposal is never re-made and
        a group somebody refused as this person is never proposed; see `Store.propose_pile`.

        No `reason` argument, although the row has one: the only caller of this seam is the folder
        reader, so the reason is always the folder, and a parameter with one right value is only a
        way to pass a wrong one. A second reason arrives with a second caller, not as a string here.
        """
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
    """One folder's counts out of what was read for every folder's files. Dissent comes after."""
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
