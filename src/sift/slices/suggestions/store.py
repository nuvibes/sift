# SPDX-License-Identifier: AGPL-3.0-or-later
"""What has been decided about the library, and a way through to what the library looks like.

Two halves with different rules, and the split is deliberate.

The **shape of the library** is read through the kernel, which owns those tables and is the only
place allowed to name them. It answers unscoped, which is what a pass needs and what a screen must
never be given: a claim is about a whole folder, and one built from the half of it some user
happens to be allowed to see would be a claim about a different folder. Every read that does reach
a screen goes back through the resolver, in the service, against whoever is asking.

The **decisions** are this feature's own rows, and they are all that is written here. Nothing in
this file interprets them; the ladder does that.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

from sift.kernel.content import FolderNode, TreeReads
from sift.kernel.db import Connection, Database, IntegrityError, Row, in_clause
from sift.kernel.ids import new_id


@dataclass(frozen=True, slots=True)
class Claim:
    """One row of outstanding or settled work."""

    id: str
    folder_id: str
    kind: str
    name_key: str
    proposed: str
    person_id: str | None
    group_id: str | None
    site: str | None
    #: Whether `proposed` is a username on `site` rather than a person's name.
    is_username: bool
    evidence: str
    state: str
    created_at: int


@dataclass(frozen=True, slots=True)
class NameFiling:
    """One file filed under a username because of its own name, as the row stands.

    Everything the row holds, so a take-back of the whole username can write it back exactly when
    it is undone: which of the pass's two words filed it, when, and in which post.
    """

    asset_id: str
    source: str
    decided_at: int | None
    post_id: str | None


def claim_from_row(row: Row) -> Claim:
    return Claim(
        id=str(row["id"]),
        folder_id=str(row["folder_id"]),
        kind=str(row["kind"]),
        name_key=str(row["name_key"]),
        proposed=str(row["proposed"]),
        person_id=None if row["person_id"] is None else str(row["person_id"]),
        group_id=None if row["group_id"] is None else str(row["group_id"]),
        site=None if row["site"] is None else str(row["site"]),
        is_username=bool(row["is_username"]),
        evidence=str(row["evidence"]),
        state=str(row["state"]),
        created_at=int(row["created_at"]),
    )


_SITE_NAMES = "SELECT name FROM sites"
#: Which Site a Username is on, and which Site answers to a name: who a filing must tell.
_SITE_OF_USERNAME = "SELECT site_id FROM usernames WHERE id = ?"
_SITE_NAMED = "SELECT id FROM sites WHERE name = ?"

# Every person's name, for the near-miss check. A list rather than a query per claim: the question
# is "is there somebody almost called this", which cannot be asked of an index.
_PEOPLE_NAMES = "SELECT id, name FROM people"

_SIGNATURES = "SELECT folder_id, signature FROM folder_passes"
_WRITE_SIGNATURE = (
    "INSERT INTO folder_passes (folder_id, signature, scanned_at) VALUES (?, ?, ?) "
    "ON CONFLICT(folder_id) DO UPDATE SET signature = excluded.signature, "
    "scanned_at = excluded.scanned_at"
)

_INSERT_CLAIM = """
INSERT INTO folder_claims
  (id, folder_id, kind, name_key, proposed, person_id, group_id, site, is_username,
   evidence, state, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
ON CONFLICT(folder_id, name_key) DO NOTHING
"""

# A claim still on the screen takes the folder's spelling as it is read now: the reader can change
# how it spells a name (folding every name to lower case, say), and a pending row written by an
# older reading would otherwise keep the old spelling for ever. Answered rows keep what was answered.
_RESPELL_PENDING_CLAIM = """
UPDATE folder_claims SET proposed = ?
 WHERE folder_id = ? AND name_key = ? AND state = 'pending' AND proposed <> ?
"""

# What has already been said about this folder, whatever was said. A claim in any state at all is a
# claim not to raise again: pending because it is already on the screen, settled because it has
# been answered.
_CLAIMS_FOR_FOLDER = "SELECT * FROM folder_claims WHERE folder_id = ?"
_CLAIM_BY_ID = "SELECT * FROM folder_claims WHERE id = ?"
_EVERY_PENDING_CLAIM = "SELECT * FROM folder_claims WHERE state = 'pending'"

# A question the folder's reading no longer asks. Pending only: an answered claim is somebody's
# word, and it stays whatever the reader now makes of the folder.
_RETIRE_CLAIM = "DELETE FROM folder_claims WHERE id = ? AND state = 'pending'"
_PENDING_CLAIMS = "SELECT * FROM folder_claims WHERE state = 'pending' ORDER BY id LIMIT ? OFFSET ?"
_SETTLE_CLAIM = "UPDATE folder_claims SET state = ?, decided_at = ? WHERE id = ? AND state = ?"

_REJECT_NAME = (
    "INSERT INTO claim_rejections (name_key, created_at) VALUES (?, ?) "
    "ON CONFLICT(name_key) DO NOTHING"
)
_REJECTED_NAMES = "SELECT name_key FROM claim_rejections"
_FORGET_REJECTION = "DELETE FROM claim_rejections WHERE name_key = ?"

# Putting a settled claim back among the questions, for a decision that is being taken back. The
# `state != 'pending'` is the mirror of the settle above: reopening one that is already open is not
# a second undo, it is nothing, and it answers false rather than moving `decided_at` about.
_REOPEN_CLAIM = (
    "UPDATE folder_claims SET state = 'pending', decided_at = NULL"
    " WHERE id = ? AND state != 'pending'"
)

_REMEMBER_FOLDER_PERSON = (
    "INSERT INTO folder_people (folder_id, person_id, created_at) VALUES (?, ?, ?) "
    "ON CONFLICT(folder_id, person_id) DO NOTHING"
)
_PEOPLE_OF_FOLDERS = "SELECT folder_id, person_id FROM folder_people"
_FORGET_FOLDER_PERSON = "DELETE FROM folder_people WHERE folder_id = ? AND person_id = ?"

# The standing no to a silent write: this folder is not to be filed under this person without
# somebody saying so. See `schema._CREATE_FOLDER_REFUSALS`.
# Only for a folder and a person still here: an Undo pressed on the record of a folder deleted
# since has nothing to refuse, and the row would name nothing. The folder is asked of the kernel
# (`refuse_folder_person_on`) on the same connection; the person here.
_REFUSE_FOLDER_PERSON = (
    "INSERT INTO folder_refusals (folder_id, person_id, created_at) SELECT ?, ?, ? "
    "WHERE EXISTS (SELECT 1 FROM people WHERE id = ?) "
    "ON CONFLICT(folder_id, person_id) DO NOTHING"
)
_FOLDER_REFUSALS = "SELECT folder_id, person_id FROM folder_refusals"
_FORGET_FOLDER_REFUSAL = "DELETE FROM folder_refusals WHERE folder_id = ? AND person_id = ?"
_FOLDER_PERSON_STANDS = "SELECT 1 FROM folder_people WHERE folder_id = ? AND person_id = ?"
#: Which of these files carry this person because a folder pass put them there: the rows a take
#: back of a folder may touch, and never one a person or another pass wrote.
_FILED_BY_A_FOLDER = (
    "SELECT asset_id FROM asset_people WHERE person_id = ? AND source = ? AND asset_id IN (?*)"
)
#: What a receipt of a silent write calls the person, read inside the write (and the folder,
#: through the kernel's `TreeReads.said_on`).
_PERSON_NAME = "SELECT name FROM people WHERE id = ?"

# Folders answered as a site. Their people come one per file out of the filenames rather than one
# for the folder, so there is no `folder_people` row to carry the answer forward. This is what
# does it instead.
_CONFIRMED_SITE_FOLDERS = (
    "SELECT folder_id FROM folder_claims WHERE kind = 'site' AND state = 'confirmed'"
)


#: Take back exactly what a pass filed, and nothing else.
#:
#: Three conditions and every one of them is load-bearing. The USERNAME, because an undo is about
#: one decision and a file may sit under several. The SOURCE, because a file somebody filed here
#: themselves carries no word and was never this pass's to remove: the insert keeps the first
#: answer, so such a row is not one this decision wrote even though it names the same pair. And the
#: FILES the decision wrote down, because a file filed under this username since by anything else is
#: not part of what is being taken back.
_UNFILE_FILING = (
    "DELETE FROM asset_usernames WHERE username_id = ? AND source = ? AND asset_id IN (?*)"
)

#: The same DELETE for the pass that reads a file's own NAME, which writes two words rather than
#: one (see `service.FILED_FROM_A_NAME`). Its own statement rather than a parameter on the one
#: above, because the two say different things: the folder pass writes exactly `folder` and naming
#: it twice would read as a mistake, and a decision of the filename pass does not record which of
#: its two words it used, so its undo has to name both or report putting back nothing while the
#: filing sits there.
_UNFILE_FROM_A_NAME = (
    "DELETE FROM asset_usernames WHERE username_id = ? AND source IN (?, ?) AND asset_id IN (?*)"
)

#: The permanent no for a filing read off a file's own name. See `schema._CREATE_FILENAME_REFUSALS`
#: for why it is keyed by the file where the folder rejection is keyed by the name.
#:
#: `DO NOTHING`, because the pass cannot file a refused file again and so cannot write a second
#: refusal, but a database restored from a backup taken before an undo can, and the first refusal
#: is the one that happened.
_REFUSE_FILENAME = (
    "INSERT INTO filename_refusals (asset_id, created_at) VALUES (?, ?) "
    "ON CONFLICT(asset_id) DO NOTHING"
)
_REFUSED_FILENAMES = "SELECT asset_id FROM filename_refusals"

#: The numbers no username has been found for, replaced whole each pass. See the table's own
#: note in `schema.py` for why the card cannot work this out for itself.
_FORGET_WAITING_NUMBERS = "DELETE FROM username_numbers_waiting"
_REMEMBER_WAITING_NUMBER = (
    "INSERT INTO username_numbers_waiting (site, number, files, seen_at) VALUES (?, ?, ?, ?)"
)
_WAITING_NUMBERS = "SELECT site, number, files FROM username_numbers_waiting ORDER BY files DESC"
_COUNT_WAITING_NUMBERS = "SELECT COUNT(*) AS total FROM username_numbers_waiting"

#: How many files one DELETE names at a time.
#:
#: A bound placeholder is a bound placeholder and SQLite counts them: `SQLITE_MAX_VARIABLE_NUMBER`
#: is 32,766 on every build Sift runs against today and was 999 on builds still in the wild, and a
#: single decision here can name three thousand files. Chunking costs a handful of statements inside
#: one transaction and takes the limit out of the question entirely, which is cheaper than finding
#: out on somebody else's machine which number their SQLite was compiled with.
_UNFILE_AT_A_TIME = 500

#: Every filing a file's own name made under one username: what taking the whole username back
#: removes, and so what its Undo writes back.
_FILED_FROM_A_NAME_UNDER = (
    "SELECT asset_id, source, decided_at, post_id FROM asset_usernames "
    "WHERE username_id = ? AND source IN (?, ?) ORDER BY asset_id"
)

#: A filing written back as it was, when taking a username back is undone. `DO NOTHING`, as the
#: pass's own insert has it: a filing made since then is a newer fact and stands. Which files and
#: which username may come back is the service's read through the kernel, made before this runs.
_REFILE_FROM_A_NAME = (
    "INSERT INTO asset_usernames (asset_id, username_id, source, decided_at, post_id) "
    "VALUES (?, ?, ?, ?, ?) "
    "ON CONFLICT(asset_id, username_id) DO NOTHING"
)

#: The standing no taken off again, for the files an undone take-back had refused.
_UNREFUSE_FILENAME = "DELETE FROM filename_refusals WHERE asset_id IN (?*)"


#: What the pass filed, and how much of it.
#:
#: Counted from `asset_people` rather than from the folder, because the folder's size is not what
#: was written: the faces veto files inside it, so the two numbers genuinely differ and the honest
#: one is the number of rows this feature actually put there.
#: A Yes writes the same standing answer, but a person answered it, and its Undo is its own.
_FILED_WITHOUT_ASKING = """
SELECT fp.folder_id AS folder_id, fp.person_id AS person_id
  FROM folder_people fp
 WHERE NOT EXISTS (SELECT 1 FROM folder_claims c
                    WHERE c.folder_id = fp.folder_id
                      AND c.kind = 'person'
                      AND c.state = 'confirmed')
 -- ordered by the clock: a filing has no id of its own; its key is the folder and the person
 ORDER BY fp.created_at DESC, fp.folder_id ASC
"""


class Store:
    """Everything this feature reads and writes, and nothing that decides anything."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self._tree = TreeReads(database)

    @property
    def database(self) -> Database:
        """The one handle this feature has, for the stash-box helpers that take one."""
        return self._db

    @staticmethod
    def _now() -> int:
        return int(time.time())

    # --- the library's shape, through the kernel ------------------------------------------------

    async def folders_with_files(self) -> list[FolderNode]:
        return await self._tree.folders_with_files()

    async def assets_under(self, folder_id: str) -> list[str]:
        return await self._tree.assets_under(folder_id)

    async def filenames_in(self, folder_id: str) -> list[str]:
        return await self._tree.filenames_in(folder_id)

    async def filenames_by_folder(self, folder_ids: Sequence[str]) -> dict[str, list[str]]:
        return await self._tree.filenames_by_folder(folder_ids)

    async def files_in(self, folder_id: str) -> list[tuple[str, str]]:
        return await self._tree.files_in(folder_id)

    async def files_in_on(self, connection: Connection, folder_id: str) -> list[tuple[str, str]]:
        return await self._tree.files_in_on(connection, folder_id)

    async def folder_ids(self) -> dict[tuple[str, str], str]:
        return await self._tree.folder_ids()

    # --- the stash-box's own vocabulary --------------------------------------------------------

    async def site_names(self) -> list[str]:
        rows = await self._db.fetch_all(_SITE_NAMES)
        return [str(row["name"]) for row in rows]

    async def sites_of_on(
        self, connection: Connection, *, usernames: Sequence[str] = (), named: Sequence[str] = ()
    ) -> set[str]:
        """The Sites these Usernames are on and these names are, read inside a write: the Sites a
        filing made in it moved, so the write can tell whoever may see them."""
        found: set[str] = set()
        for username_id in usernames:
            for row in await connection.execute_fetchall(_SITE_OF_USERNAME, (username_id,)):
                if row["site_id"] is not None:
                    found.add(str(row["site_id"]))
        for name in named:
            for row in await connection.execute_fetchall(_SITE_NAMED, (name,)):
                found.add(str(row["id"]))
        return found

    async def people_names(self) -> list[tuple[str, str]]:
        rows = await self._db.fetch_all(_PEOPLE_NAMES)
        return [(str(row["id"]), str(row["name"])) for row in rows]

    async def people_names_on(self, connection: Connection) -> list[tuple[str, str]]:
        """The same list, on a connection the caller already holds.

        Attributing a folder is one action made of several writes and it runs inside one
        transaction, so a read it needs has to go through that connection. The write guard is not
        reentrant, and asking for a second one from inside the first deadlocks.
        """
        rows = await (await connection.execute(_PEOPLE_NAMES)).fetchall()
        return [(str(row["id"]), str(row["name"])) for row in rows]

    # --- what the last pass saw ---------------------------------------------------------------

    async def signatures(self) -> dict[str, str]:
        rows = await self._db.fetch_all(_SIGNATURES)
        return {str(row["folder_id"]): str(row["signature"]) for row in rows}

    async def close_pass(
        self, *, retired: Sequence[str], signatures: Sequence[tuple[str, str]]
    ) -> int:
        """Take back the questions a pass no longer asks and remember what it read, in one write.

        One write, so a pass that stops part way leaves every folder it read unremembered and the
        next pass reads them again: a folder is never marked read with its stale questions left
        standing. Returns how many questions were taken back.
        """
        now = self._now()
        async with self._db.write() as connection:
            removed = 0
            for claim_id in retired:
                cursor = await connection.execute(_RETIRE_CLAIM, (claim_id,))
                removed += int(cursor.rowcount or 0)
            for folder_id, signature in signatures:
                await connection.execute(_WRITE_SIGNATURE, (folder_id, signature, now))
            return removed

    # --- claims -------------------------------------------------------------------------------

    async def add_claim(
        self,
        *,
        folder_id: str,
        kind: str,
        name_key: str,
        proposed: str,
        person_id: str | None,
        group_id: str | None,
        site: str | None,
        is_username: bool,
        evidence: str,
    ) -> int:
        """File a claim. Returns 1, or 0 when this folder has already made this one in any state.

        A pending claim already made is re-spelled to `proposed` where the spelling moved, which
        counts as nothing written: the claim was there.
        """
        async with self._db.write() as connection:
            await connection.execute(
                _RESPELL_PENDING_CLAIM, (proposed, folder_id, name_key, proposed)
            )
            cursor = await connection.execute(
                _INSERT_CLAIM,
                (
                    new_id(),
                    folder_id,
                    kind,
                    name_key,
                    proposed,
                    person_id,
                    group_id,
                    site,
                    1 if is_username else 0,
                    evidence,
                    self._now(),
                ),
            )
            return int(cursor.rowcount or 0)

    async def claims_for(self, folder_id: str) -> list[Claim]:
        rows = await self._db.fetch_all(_CLAIMS_FOR_FOLDER, (folder_id,))
        return [claim_from_row(row) for row in rows]

    async def claim(self, claim_id: str) -> Claim | None:
        row = await self._db.fetch_one(_CLAIM_BY_ID, (claim_id,))
        return None if row is None else claim_from_row(row)

    async def every_pending(self) -> list[Claim]:
        rows = await self._db.fetch_all(_EVERY_PENDING_CLAIM)
        return [claim_from_row(row) for row in rows]

    async def pending(self, *, limit: int, offset: int) -> list[Claim]:
        rows = await self._db.fetch_all(_PENDING_CLAIMS, (limit, offset))
        return [claim_from_row(row) for row in rows]

    async def settle_on(self, connection: Connection, claim_id: str, state: str) -> bool:
        """Move a claim off pending, on a caller's connection. False when it had already moved.

        The `state = 'pending'` in the update is what makes two people pressing the same button at
        once settle it once: the second update matches nothing and answers false, rather than
        overwriting the first answer with the second.
        """
        cursor = await connection.execute(_SETTLE_CLAIM, (state, self._now(), claim_id, "pending"))
        return bool(cursor.rowcount)

    # --- the permanent no ---------------------------------------------------------------------

    async def reopen_on(self, connection: Connection, claim_id: str) -> bool:
        """Put a settled claim back among the questions. False when it was already there."""
        cursor = await connection.execute(_REOPEN_CLAIM, (claim_id,))
        return bool(cursor.rowcount)

    async def reject_name_on(self, connection: Connection, name_key: str) -> None:
        await connection.execute(_REJECT_NAME, (name_key, self._now()))

    async def forget_rejection_on(self, connection: Connection, name_key: str) -> None:
        """Take back a permanent no. The one thing that puts a set-aside folder back on offer."""
        await connection.execute(_FORGET_REJECTION, (name_key,))

    async def rejected_names(self) -> set[str]:
        rows = await self._db.fetch_all(_REJECTED_NAMES)
        return {str(row["name_key"]) for row in rows}

    # --- the standing yes ---------------------------------------------------------------------

    async def remember_folder_person_on(
        self, connection: Connection, *, folder_id: str, person_id: str
    ) -> bool:
        """Remember who a folder is. True when this made the answer, False when it stood already."""
        cursor = await connection.execute(
            _REMEMBER_FOLDER_PERSON, (folder_id, person_id, self._now())
        )
        return bool(cursor.rowcount)

    async def refuse_folder_person_on(
        self, connection: Connection, *, folder_id: str, person_id: str
    ) -> bool:
        """Say no, for good, to the pass filing this folder under this person without asking.
        True when this said it, False when it stood already or the folder or person has gone.

        What taking back a silent write leaves behind, for the reason `forget_folder_person_on`
        gives about the standing yes: the faces that filed the folder are still there on the next
        pass, and without this the same write would come straight back.
        """
        if await self._tree.said_on(connection, folder_id) is None:
            return False
        cursor = await connection.execute(
            _REFUSE_FOLDER_PERSON, (folder_id, person_id, self._now(), person_id)
        )
        return bool(cursor.rowcount)

    async def forget_folder_refusal_on(
        self, connection: Connection, *, folder_id: str, person_id: str
    ) -> None:
        """Forget that no, for the Undo of the press that said it."""
        await connection.execute(_FORGET_FOLDER_REFUSAL, (folder_id, person_id))

    async def folder_person_stands(self, folder_id: str, person_id: str) -> bool:
        """Whether this folder is answered as this person now."""
        return await self._db.fetch_one(_FOLDER_PERSON_STANDS, (folder_id, person_id)) is not None

    async def filed_by_a_folder(
        self, person_id: str, asset_ids: Sequence[str], *, source: str
    ) -> list[str]:
        """Which of these files carry this person with `source` (the folder pass's word)."""
        wanted = list(dict.fromkeys(asset_ids))
        found: list[str] = []
        for at in range(0, len(wanted), _UNFILE_AT_A_TIME):
            query, bound = in_clause(_FILED_BY_A_FOLDER, wanted[at : at + _UNFILE_AT_A_TIME])
            rows = await self._db.fetch_all(query, [person_id, source, *bound])
            found += [str(row["asset_id"]) for row in rows]
        return found

    async def folder_refusals(self) -> set[tuple[str, str]]:
        """Every `(folder, person)` the pass may not file without asking."""
        rows = await self._db.fetch_all(_FOLDER_REFUSALS)
        return {(str(row["folder_id"]), str(row["person_id"])) for row in rows}

    async def person_name_on(self, connection: Connection, person_id: str) -> str:
        row = await (await connection.execute(_PERSON_NAME, (person_id,))).fetchone()
        return "" if row is None else str(row["name"])

    async def folder_said_on(self, connection: Connection, folder_id: str) -> str:
        """A folder as a line says it: its path inside the library, or its name for the top one."""
        return await self._tree.said_on(connection, folder_id) or ""

    async def forget_folder_person_on(
        self, connection: Connection, *, folder_id: str, person_id: str
    ) -> bool:
        """Forget a standing answer, for a decision being taken back.

        Load-bearing rather than tidy. The answer is what re-applies a person to files that land in
        the folder later, so leaving it behind would have the next pass put back exactly what the
        undo has just removed, and there would be nothing on any screen saying why.
        """
        cursor = await connection.execute(_FORGET_FOLDER_PERSON, (folder_id, person_id))
        return bool(cursor.rowcount)

    async def confirmed_site_folders(self) -> set[str]:
        rows = await self._db.fetch_all(_CONFIRMED_SITE_FOLDERS)
        return {str(row["folder_id"]) for row in rows}

    async def filed_without_asking(self) -> list[tuple[str, str]]:
        """Every person a pass filed a folder under: folder id and person id, newest first.

        Ids only. Who may be told about a person or a folder is the resolver's question, and how
        many of the folder's files carry the person is the kernel's: it reads the asset tables
        through the viewer's verdict, which this table knows nothing about.
        """
        rows = await self._db.fetch_all(_FILED_WITHOUT_ASKING)
        return [(str(r["folder_id"]), str(r["person_id"])) for r in rows]

    async def unfile_on(
        self, connection: Connection, *, username_id: str, source: str, asset_ids: Sequence[str]
    ) -> int:
        """Remove the filings one decision wrote. Returns how many rows went.

        On the caller's connection, because taking a decision back is one action and the receipt
        saying it was taken back is written in the same transaction.
        """
        wanted = list(asset_ids)
        removed = 0
        for at in range(0, len(wanted), _UNFILE_AT_A_TIME):
            page = wanted[at : at + _UNFILE_AT_A_TIME]
            statement, bound = in_clause(_UNFILE_FILING, page)
            cursor = await connection.execute(statement, [username_id, source, *bound])
            removed += int(cursor.rowcount or 0)
        return removed

    async def unfile_from_a_name_on(
        self,
        connection: Connection,
        *,
        username_id: str,
        sources: tuple[str, str],
        asset_ids: Sequence[str],
    ) -> int:
        """Remove the filings one filename decision wrote, under either of the pass's two words.

        The shape `unfile_on` has, chunked for the same reason: one decision here can name three
        thousand files and a bound placeholder is a bound placeholder.
        """
        wanted = list(asset_ids)
        removed = 0
        for at in range(0, len(wanted), _UNFILE_AT_A_TIME):
            page = wanted[at : at + _UNFILE_AT_A_TIME]
            statement, bound = in_clause(_UNFILE_FROM_A_NAME, page)
            cursor = await connection.execute(statement, [username_id, *sources, *bound])
            removed += int(cursor.rowcount or 0)
        return removed

    async def filed_from_a_name_under(
        self, username_id: str, sources: tuple[str, str]
    ) -> list[NameFiling]:
        """Every file filed under this username by one of the pass's two words, row by row."""
        rows = await self._db.fetch_all(_FILED_FROM_A_NAME_UNDER, (username_id, *sources))
        return [
            NameFiling(
                asset_id=str(row["asset_id"]),
                source=str(row["source"]),
                decided_at=None if row["decided_at"] is None else int(row["decided_at"]),
                post_id=None if row["post_id"] is None else str(row["post_id"]),
            )
            for row in rows
        ]

    async def refile_from_a_name_on(
        self, connection: Connection, username_id: str, filings: Sequence[NameFiling]
    ) -> int:
        """Write these filings back as they were. How many rows were really written.

        A file or a username deleted after the service's read fails its row's foreign key, which
        undoes that one statement and nothing else, so it is skipped as one deleted before it is.
        """
        written = 0
        for one in filings:
            try:
                cursor = await connection.execute(
                    _REFILE_FROM_A_NAME,
                    (one.asset_id, username_id, one.source, one.decided_at, one.post_id),
                )
            except IntegrityError:
                continue
            written += int(cursor.rowcount or 0)
        return written

    async def unrefuse_filename_on(self, connection: Connection, asset_ids: Sequence[str]) -> None:
        """Forget the standing no on these files, so the pass may read their names again."""
        wanted = list(asset_ids)
        for at in range(0, len(wanted), _UNFILE_AT_A_TIME):
            statement, bound = in_clause(_UNREFUSE_FILENAME, wanted[at : at + _UNFILE_AT_A_TIME])
            await connection.execute(statement, bound)

    async def refuse_filename_on(self, connection: Connection, asset_ids: Sequence[str]) -> None:
        """Remember that these files are not to be filed from their own names again.

        Load-bearing rather than tidy, exactly as `forget_folder_person_on` is and for the mirror
        reason. The pass reads every file under no site at all, so a file whose filing has just
        been taken back is a candidate again the moment the undo lands, and the next pass would
        put back precisely what somebody just removed, with nothing on any screen saying why.

        On the caller's connection, because the undo and the memory of it are one action: a refusal
        written separately could be present for a filing that is still there, or absent for one
        that has gone.
        """
        now = self._now()
        await connection.executemany(_REFUSE_FILENAME, [(one, now) for one in asset_ids])

    async def refused_filenames(self) -> set[str]:
        """Every file somebody has taken a filename filing back from. Read whole, by the pass.

        Whole rather than asked per candidate, for the reason the candidate read itself is whole:
        this is one query against a table that only grows by hand, and the alternative is a query
        per file across a library of a hundred thousand.
        """
        rows = await self._db.fetch_all(_REFUSED_FILENAMES)
        return {str(row["asset_id"]) for row in rows}

    async def remember_waiting_numbers(self, waiting: Sequence[tuple[str, str, int]]) -> None:
        """Replace the list of username numbers this library cannot name. `(site, number, files)`.

        Replaced whole rather than reconciled, and in ONE transaction, because the pass that writes
        it has just walked every filename in the library, so what it holds IS the answer, and a
        row surviving from a previous pass would be a number that is no longer waiting. A number
        that has since been named simply is not in what arrives.
        """
        async with self.write() as connection:
            await connection.execute(_FORGET_WAITING_NUMBERS)
            if waiting:
                now = self._now()
                await connection.executemany(
                    _REMEMBER_WAITING_NUMBER,
                    [(site, number, files, now) for site, number, files in waiting],
                )

    async def waiting_numbers(self) -> list[tuple[str, str, int]]:
        """Every number waiting for a username, most files first."""
        rows = await self._db.fetch_all(_WAITING_NUMBERS)
        return [(str(row["site"]), str(row["number"]), int(row["files"])) for row in rows]

    async def count_waiting_numbers(self) -> int:
        """How many there are. What the card says, in one indexed count."""
        row = await self._db.fetch_one(_COUNT_WAITING_NUMBERS)
        return 0 if row is None else int(row["total"])

    async def folder_people(self) -> dict[str, set[str]]:
        rows = await self._db.fetch_all(_PEOPLE_OF_FOLDERS)
        answered: dict[str, set[str]] = {}
        for row in rows:
            answered.setdefault(str(row["folder_id"]), set()).add(str(row["person_id"]))
        return answered

    # --- the one transaction ------------------------------------------------------------------

    def write(self) -> AbstractAsyncContextManager[Connection]:
        """The write guard, handed out so the service can put several writes in one transaction.

        Returned rather than wrapped, because what has to be atomic spans this feature's tables and
        the stash-box's, and the guard is not reentrant, so there can only be one holder of it.
        """
        return self._db.write()
