# SPDX-License-Identifier: AGPL-3.0-or-later
"""The folder suggestions' own rows, plus unscoped reads of the library's shape through the
kernel."""

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
    id: str
    folder_id: str
    kind: str
    name_key: str
    proposed: str
    person_id: str | None
    group_id: str | None
    site: str | None
    is_username: bool
    evidence: str
    state: str
    created_at: int


@dataclass(frozen=True, slots=True)
class NameFiling:
    """One file's filing from its own name, whole, so a username's take-back can restore it."""

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
#: The Sites a filing must tell.
_SITE_OF_USERNAME = "SELECT site_id FROM usernames WHERE id = ?"
_SITE_NAMED = "SELECT id FROM sites WHERE name = ?"

# Every name, since "almost called this" cannot be asked of an index.
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

# Pending claims follow the reader's current spelling; answered ones keep theirs.
_RESPELL_PENDING_CLAIM = """
UPDATE folder_claims SET proposed = ?
 WHERE folder_id = ? AND name_key = ? AND state = 'pending' AND proposed <> ?
"""

# A claim in any state is not raised again.
_CLAIMS_FOR_FOLDER = "SELECT * FROM folder_claims WHERE folder_id = ?"
_CLAIM_BY_ID = "SELECT * FROM folder_claims WHERE id = ?"
_EVERY_PENDING_CLAIM = "SELECT * FROM folder_claims WHERE state = 'pending'"

# Pending only: an answered claim is somebody's word.
_RETIRE_CLAIM = "DELETE FROM folder_claims WHERE id = ? AND state = 'pending'"
_PENDING_CLAIMS = "SELECT * FROM folder_claims WHERE state = 'pending' ORDER BY id LIMIT ? OFFSET ?"
_SETTLE_CLAIM = "UPDATE folder_claims SET state = ?, decided_at = ? WHERE id = ? AND state = ?"

_REJECT_NAME = (
    "INSERT INTO claim_rejections (name_key, created_at) VALUES (?, ?) "
    "ON CONFLICT(name_key) DO NOTHING"
)
_REJECTED_NAMES = "SELECT name_key FROM claim_rejections"
_FORGET_REJECTION = "DELETE FROM claim_rejections WHERE name_key = ?"

# Reopening an open claim is nothing, and answers false.
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

# Only for a folder and person still here (see `schema._CREATE_FOLDER_REFUSALS`).
_REFUSE_FOLDER_PERSON = (
    "INSERT INTO folder_refusals (folder_id, person_id, created_at) SELECT ?, ?, ? "
    "WHERE EXISTS (SELECT 1 FROM people WHERE id = ?) "
    "ON CONFLICT(folder_id, person_id) DO NOTHING"
)
_FOLDER_REFUSALS = "SELECT folder_id, person_id FROM folder_refusals"
_FORGET_FOLDER_REFUSAL = "DELETE FROM folder_refusals WHERE folder_id = ? AND person_id = ?"
_FOLDER_PERSON_STANDS = "SELECT 1 FROM folder_people WHERE folder_id = ? AND person_id = ?"
#: Only rows a folder pass wrote.
_FILED_BY_A_FOLDER = (
    "SELECT asset_id FROM asset_people WHERE person_id = ? AND source = ? AND asset_id IN (?*)"
)
_PERSON_NAME = "SELECT name FROM people WHERE id = ?"

# Site-answered folders have no `folder_people` row, so this carries them forward.
_CONFIRMED_SITE_FOLDERS = (
    "SELECT folder_id FROM folder_claims WHERE kind = 'site' AND state = 'confirmed'"
)


#: Username, source word and the decision's files: each keeps the delete to what this pass wrote.
_UNFILE_FILING = (
    "DELETE FROM asset_usernames WHERE username_id = ? AND source = ? AND asset_id IN (?*)"
)

#: The filename pass writes two words, and its decisions do not record which.
_UNFILE_FROM_A_NAME = (
    "DELETE FROM asset_usernames WHERE username_id = ? AND source IN (?, ?) AND asset_id IN (?*)"
)

#: `DO NOTHING`: a database restored from before an undo can refuse twice.
_REFUSE_FILENAME = (
    "INSERT INTO filename_refusals (asset_id, created_at) VALUES (?, ?) "
    "ON CONFLICT(asset_id) DO NOTHING"
)
_REFUSED_FILENAMES = "SELECT asset_id FROM filename_refusals"

#: Replaced whole each pass.
_FORGET_WAITING_NUMBERS = "DELETE FROM username_numbers_waiting"
_REMEMBER_WAITING_NUMBER = (
    "INSERT INTO username_numbers_waiting (site, number, files, seen_at) VALUES (?, ?, ?, ?)"
)
_WAITING_NUMBERS = "SELECT site, number, files FROM username_numbers_waiting ORDER BY files DESC"
_COUNT_WAITING_NUMBERS = "SELECT COUNT(*) AS total FROM username_numbers_waiting"

#: Older SQLite builds cap a statement at 999 variables.
_UNFILE_AT_A_TIME = 500

_FILED_FROM_A_NAME_UNDER = (
    "SELECT asset_id, source, decided_at, post_id FROM asset_usernames "
    "WHERE username_id = ? AND source IN (?, ?) ORDER BY asset_id"
)

#: `DO NOTHING`: a filing made since is newer and stands.
_REFILE_FROM_A_NAME = (
    "INSERT INTO asset_usernames (asset_id, username_id, source, decided_at, post_id) "
    "VALUES (?, ?, ?, ?, ?) "
    "ON CONFLICT(asset_id, username_id) DO NOTHING"
)

_UNREFUSE_FILENAME = "DELETE FROM filename_refusals WHERE asset_id IN (?*)"


#: Counted from `asset_people`: faces veto files, so the folder's size is not what was written.
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
    def __init__(self, database: Database) -> None:
        self._db = database
        self._tree = TreeReads(database)

    @property
    def database(self) -> Database:
        return self._db

    @staticmethod
    def _now() -> int:
        return int(time.time())

    async def folders_with_files(self) -> list[FolderNode]:
        return await self._tree.folders_with_files()

    async def assets_under(self, folder_id: str) -> list[str]:
        return await self._tree.assets_under(folder_id)

    async def assets_present_under(self, folder_id: str) -> list[str]:
        return await self._tree.assets_present_under(folder_id)

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

    async def site_names(self) -> list[str]:
        rows = await self._db.fetch_all(_SITE_NAMES)
        return [str(row["name"]) for row in rows]

    async def sites_of_on(
        self, connection: Connection, *, usernames: Sequence[str] = (), named: Sequence[str] = ()
    ) -> set[str]:
        """The Sites these Usernames are on and these names are, read inside a write."""
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
        """The same list on the caller's connection: the write guard is not reentrant."""
        rows = await (await connection.execute(_PEOPLE_NAMES)).fetchall()
        return [(str(row["id"]), str(row["name"])) for row in rows]

    async def signatures(self) -> dict[str, str]:
        rows = await self._db.fetch_all(_SIGNATURES)
        return {str(row["folder_id"]): str(row["signature"]) for row in rows}

    async def close_pass(
        self, *, retired: Sequence[str], signatures: Sequence[tuple[str, str]]
    ) -> int:
        """Take back retired questions and record what was read, in one write; returns how many."""
        now = self._now()
        async with self._db.write() as connection:
            removed = 0
            for claim_id in retired:
                cursor = await connection.execute(_RETIRE_CLAIM, (claim_id,))
                removed += int(cursor.rowcount or 0)
            for folder_id, signature in signatures:
                await connection.execute(_WRITE_SIGNATURE, (folder_id, signature, now))
            return removed

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
        """File a claim; 0 when this folder already made it, re-spelling a pending one."""
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
        """Move a claim off pending; False when it already moved, so two presses settle once."""
        cursor = await connection.execute(_SETTLE_CLAIM, (state, self._now(), claim_id, "pending"))
        return bool(cursor.rowcount)

    async def reopen_on(self, connection: Connection, claim_id: str) -> bool:
        cursor = await connection.execute(_REOPEN_CLAIM, (claim_id,))
        return bool(cursor.rowcount)

    async def reject_name_on(self, connection: Connection, name_key: str) -> None:
        await connection.execute(_REJECT_NAME, (name_key, self._now()))

    async def forget_rejection_on(self, connection: Connection, name_key: str) -> None:
        """Take back a permanent no, putting a set-aside folder back on offer."""
        await connection.execute(_FORGET_REJECTION, (name_key,))

    async def rejected_names(self) -> set[str]:
        rows = await self._db.fetch_all(_REJECTED_NAMES)
        return {str(row["name_key"]) for row in rows}

    async def remember_folder_person_on(
        self, connection: Connection, *, folder_id: str, person_id: str
    ) -> bool:
        cursor = await connection.execute(
            _REMEMBER_FOLDER_PERSON, (folder_id, person_id, self._now())
        )
        return bool(cursor.rowcount)

    async def refuse_folder_person_on(
        self, connection: Connection, *, folder_id: str, person_id: str
    ) -> bool:
        """Refuse for good the pass filing this folder under this person; False if stood or gone."""
        if await self._tree.said_on(connection, folder_id) is None:
            return False
        cursor = await connection.execute(
            _REFUSE_FOLDER_PERSON, (folder_id, person_id, self._now(), person_id)
        )
        return bool(cursor.rowcount)

    async def forget_folder_refusal_on(
        self, connection: Connection, *, folder_id: str, person_id: str
    ) -> None:
        await connection.execute(_FORGET_FOLDER_REFUSAL, (folder_id, person_id))

    async def folder_person_stands(self, folder_id: str, person_id: str) -> bool:
        return await self._db.fetch_one(_FOLDER_PERSON_STANDS, (folder_id, person_id)) is not None

    async def filed_by_a_folder(
        self, person_id: str, asset_ids: Sequence[str], *, source: str
    ) -> list[str]:
        wanted = list(dict.fromkeys(asset_ids))
        found: list[str] = []
        for at in range(0, len(wanted), _UNFILE_AT_A_TIME):
            query, bound = in_clause(_FILED_BY_A_FOLDER, wanted[at : at + _UNFILE_AT_A_TIME])
            rows = await self._db.fetch_all(query, [person_id, source, *bound])
            found += [str(row["asset_id"]) for row in rows]
        return found

    async def folder_refusals(self) -> set[tuple[str, str]]:
        rows = await self._db.fetch_all(_FOLDER_REFUSALS)
        return {(str(row["folder_id"]), str(row["person_id"])) for row in rows}

    async def person_name_on(self, connection: Connection, person_id: str) -> str:
        row = await (await connection.execute(_PERSON_NAME, (person_id,))).fetchone()
        return "" if row is None else str(row["name"])

    async def folder_said_on(self, connection: Connection, folder_id: str) -> str:
        return await self._tree.said_on(connection, folder_id) or ""

    async def forget_folder_person_on(
        self, connection: Connection, *, folder_id: str, person_id: str
    ) -> bool:
        """Forget a standing answer, so the next pass does not put back what an undo removed."""
        cursor = await connection.execute(_FORGET_FOLDER_PERSON, (folder_id, person_id))
        return bool(cursor.rowcount)

    async def confirmed_site_folders(self) -> set[str]:
        rows = await self._db.fetch_all(_CONFIRMED_SITE_FOLDERS)
        return {str(row["folder_id"]) for row in rows}

    async def filed_without_asking(self) -> list[tuple[str, str]]:
        """Every folder a pass filed under a person, newest first, as ids only."""
        rows = await self._db.fetch_all(_FILED_WITHOUT_ASKING)
        return [(str(r["folder_id"]), str(r["person_id"])) for r in rows]

    async def unfile_on(
        self, connection: Connection, *, username_id: str, source: str, asset_ids: Sequence[str]
    ) -> int:
        """Remove the filings one decision wrote, in the caller's transaction; returns the count."""
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
        """Remove one filename decision's filings under either of the pass's two words."""
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
        """Write these filings back; rows for files or usernames deleted since are skipped."""
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
        wanted = list(asset_ids)
        for at in range(0, len(wanted), _UNFILE_AT_A_TIME):
            statement, bound = in_clause(_UNREFUSE_FILENAME, wanted[at : at + _UNFILE_AT_A_TIME])
            await connection.execute(statement, bound)

    async def refuse_filename_on(self, connection: Connection, asset_ids: Sequence[str]) -> None:
        """Remember, in the undo's transaction, not to file these files from their names again."""
        now = self._now()
        await connection.executemany(_REFUSE_FILENAME, [(one, now) for one in asset_ids])

    async def refused_filenames(self) -> set[str]:
        """Every file a filename filing was taken back from, read whole."""
        rows = await self._db.fetch_all(_REFUSED_FILENAMES)
        return {str(row["asset_id"]) for row in rows}

    async def remember_waiting_numbers(self, waiting: Sequence[tuple[str, str, int]]) -> None:
        """Replace the username numbers this library cannot name, whole, in one transaction."""
        async with self.write() as connection:
            await connection.execute(_FORGET_WAITING_NUMBERS)
            if waiting:
                now = self._now()
                await connection.executemany(
                    _REMEMBER_WAITING_NUMBER,
                    [(site, number, files, now) for site, number, files in waiting],
                )

    async def waiting_numbers(self) -> list[tuple[str, str, int]]:
        rows = await self._db.fetch_all(_WAITING_NUMBERS)
        return [(str(row["site"]), str(row["number"]), int(row["files"])) for row in rows]

    async def count_waiting_numbers(self) -> int:
        row = await self._db.fetch_one(_COUNT_WAITING_NUMBERS)
        return 0 if row is None else int(row["total"])

    async def folder_people(self) -> dict[str, set[str]]:
        rows = await self._db.fetch_all(_PEOPLE_OF_FOLDERS)
        answered: dict[str, set[str]] = {}
        for row in rows:
            answered.setdefault(str(row["folder_id"]), set()).add(str(row["person_id"]))
        return answered

    def write(self) -> AbstractAsyncContextManager[Connection]:
        """The write guard, so the service can span this feature's tables and the stash-box's."""
        return self._db.write()
