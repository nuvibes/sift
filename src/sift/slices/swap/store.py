# SPDX-License-Identifier: AGPL-3.0-or-later
"""The swap slice's rows: the device, the sessions, the manifests, and each section's readers.

One module, several sections, each under its own heading: the session's own writes first (the
device row, a session's life from `waiting` to its end, the chunks a guest has verified), then
the readers the offer and the landing add under theirs. A section never writes another's rows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.db import Connection, Database, Row
from sift.kernel.ledger import Actor, record_event
from sift.kernel.vocabulary import VIA_SWAP, Subject

# ================================================================================================
# THE SESSION'S ROWS (the device, the sessions, the manifests)
# ================================================================================================

#: The states a session never leaves. A write that moves a session refuses to move one of these,
#: so an end written by one side cannot be overwritten by a late write from the other.
TERMINAL_STATES: frozenset[str] = frozenset({"done", "ended", "failed"})

#: The live states, in the order a session passes through them.
LIVE_STATES: tuple[str, ...] = ("waiting", "connected", "offered", "transferring")

_DEVICE = "SELECT device_id, key_secret_id, created_at FROM swap_device WHERE id = 1"
_INSERT_DEVICE = (
    "INSERT INTO swap_device (id, device_id, key_secret_id, created_at) VALUES (1, ?, ?, ?)"
    " ON CONFLICT(id) DO NOTHING"
)
_REPLACE_DEVICE = (
    "UPDATE swap_device SET device_id = ?, key_secret_id = ?, created_at = ? WHERE id = 1"
)

_SESSION = (
    "SELECT id, role, state, tunnel_id, peer_device, token_expires, chosen, offered_files,"
    " wanted_files, sent_files, sent_bytes, rate_bps, dest_folder_id, started_at, ended_at,"
    " end_reason, cut_off_at, two_way, back_offered, back_wanted, back_files, back_bytes"
    " FROM swap_sessions WHERE id = ?"
)
_INSERT_SESSION = (
    "INSERT INTO swap_sessions (id, role, state, tunnel_id, peer_device, token_expires, chosen,"
    " dest_folder_id, started_at, two_way) VALUES (?, ?, 'waiting', ?, ?, ?, ?, ?, ?, ?)"
)
#: What the ledger's line about an ending says: which side this was, whose device the other was,
#: and how many files moved.
_ENDED_FACTS = (
    "SELECT role, peer_device, sent_files, two_way, back_files FROM swap_sessions WHERE id = ?"
)
# Only a live row moves. `state` is compared as well as the row picked, so a session that has
# already ended stays ended whatever a late writer asks.
_MOVE = (
    "UPDATE swap_sessions SET state = ?, peer_device = COALESCE(?, peer_device)"
    " WHERE id = ? AND state IN ('waiting', 'connected', 'offered', 'transferring')"
)
_SET_OFFERED = "UPDATE swap_sessions SET offered_files = ? WHERE id = ?"
_SET_WANTED = "UPDATE swap_sessions SET wanted_files = ? WHERE id = ?"
_ADD_SENT = (
    "UPDATE swap_sessions SET sent_files = sent_files + ?, sent_bytes = sent_bytes + ? WHERE id = ?"
)
# The same three for the direction from the guest to the host (see `schema.py`).
_SET_BACK_OFFERED = "UPDATE swap_sessions SET back_offered = ? WHERE id = ?"
_SET_BACK_WANTED = "UPDATE swap_sessions SET back_wanted = ? WHERE id = ?"
_ADD_BACK = (
    "UPDATE swap_sessions SET back_files = back_files + ?, back_bytes = back_bytes + ? WHERE id = ?"
)
_SET_TWO_WAY = "UPDATE swap_sessions SET two_way = 1 WHERE id = ? AND two_way = 0"
_SET_RATE = "UPDATE swap_sessions SET rate_bps = ? WHERE id = ?"
# A live session a tunnel cut off, and the same session joined again. Only a live row: a session
# that has ended is never cut off after the fact, and never taken back up.
_CUT_OFF = (
    "UPDATE swap_sessions SET cut_off_at = ? WHERE id = ? AND cut_off_at IS NULL"
    " AND state IN ('waiting', 'connected', 'offered', 'transferring')"
)
_REJOINED = (
    "UPDATE swap_sessions SET cut_off_at = NULL WHERE id = ? AND cut_off_at IS NOT NULL"
    " AND state IN ('waiting', 'connected', 'offered', 'transferring')"
)
_END = (
    "UPDATE swap_sessions SET state = ?, end_reason = ?, ended_at = ?"
    " WHERE id = ? AND state IN ('waiting', 'connected', 'offered', 'transferring')"
)
_UNFINISHED = (
    "SELECT id FROM swap_sessions"
    " WHERE state IN ('waiting', 'connected', 'offered', 'transferring') ORDER BY id"
)

_MANIFEST = (
    "SELECT session_id, file_key, size, chunk_size, done, digest, version, staged_path, updated_at"
    " FROM swap_manifests WHERE session_id = ? AND file_key = ?"
)
_PUT_MANIFEST = (
    "INSERT INTO swap_manifests"
    " (session_id, file_key, size, chunk_size, done, digest, version, staged_path, updated_at)"
    " VALUES (?, ?, ?, ?, '[]', ?, ?, ?, ?)"
    " ON CONFLICT(session_id, file_key) DO UPDATE SET size = excluded.size,"
    " chunk_size = excluded.chunk_size, done = '[]', digest = excluded.digest,"
    " version = excluded.version, staged_path = excluded.staged_path,"
    " updated_at = excluded.updated_at"
)
# An unfinished manifest of the same file, from an earlier session with the same device, whose
# size, chunk size and digest (or the sender's version of it) all agree: the same bytes, so the
# chunks already verified are chunks of THIS file (`IS` matches the one of the two left NULL).
# Newest first (by the session's id, which is minted in order). Either side's: a host that
# received in a swap that sends and receives kept manifests as a guest does.
_ADOPTABLE = (
    "SELECT m.session_id, m.file_key, m.size, m.chunk_size, m.done, m.digest, m.version,"
    " m.staged_path, m.updated_at FROM swap_manifests m JOIN swap_sessions s ON s.id = m.session_id"
    " WHERE s.peer_device = ? AND m.file_key = ? AND m.size = ?"
    " AND m.chunk_size = ? AND m.digest IS ? AND m.version IS ? AND m.session_id <> ?"
    " ORDER BY m.session_id DESC LIMIT 1"
)
_MOVE_MANIFEST = (
    "UPDATE swap_manifests SET session_id = ?, updated_at = ? WHERE session_id = ? AND file_key = ?"
)
# One chunk appended once. `json_each` is the guard against a chunk counted twice when a resend of
# a chunk already verified arrives; `$[#]` appends.
_MARK_DONE = (
    "UPDATE swap_manifests SET done = json_insert(done, '$[#]', ?), updated_at = ?"
    " WHERE session_id = ? AND file_key = ?"
    " AND NOT EXISTS (SELECT 1 FROM json_each(swap_manifests.done) WHERE value = ?)"
)
_DROP_MANIFEST = "DELETE FROM swap_manifests WHERE session_id = ? AND file_key = ?"
_STALE_MANIFESTS = (
    "SELECT session_id, file_key, size, chunk_size, done, digest, version, staged_path, updated_at"
    " FROM swap_manifests WHERE updated_at < ? ORDER BY updated_at LIMIT ?"
)


@dataclass(frozen=True, slots=True)
class DeviceRow:
    device_id: str
    key_secret_id: str
    created_at: int


@dataclass(frozen=True, slots=True)
class SessionRow:
    """One session as its row holds it. Never the token or its secret: those are not in the row."""

    id: str
    role: str
    state: str
    tunnel_id: str | None
    peer_device: str | None
    token_expires: int | None
    chosen: list[dict[str, object]]
    offered_files: int
    wanted_files: int
    sent_files: int
    sent_bytes: int
    rate_bps: int | None
    dest_folder_id: str | None
    started_at: int
    ended_at: int | None
    end_reason: str | None
    #: When a tunnel cut the session off, or None: it is still in `state`, waiting for the same
    #: token to join again until the token runs out.
    cut_off_at: int | None = None
    #: Whether files go both ways, and the direction from the guest to the host: offered, wanted,
    #: and moved (received on the host's row, sent on the guest's). See `schema.py`.
    two_way: bool = False
    back_offered: int = 0
    back_wanted: int = 0
    back_files: int = 0
    back_bytes: int = 0

    @property
    def short_id(self) -> str:
        """The last eight characters of the id: what a log line and a History payload name."""
        return self.id[-8:]

    @property
    def live(self) -> bool:
        return self.state not in TERMINAL_STATES


@dataclass(frozen=True, slots=True)
class ManifestRow:
    session_id: str
    file_key: str
    size: int
    chunk_size: int
    done: tuple[int, ...]
    digest: str | None
    staged_path: str | None
    updated_at: int
    #: The sender's version of the file (`pieces.version_of`), where it checks the whole at the end.
    version: str | None = None


def _chosen(text: object) -> list[dict[str, object]]:
    try:
        value = json.loads(str(text or "[]"))
    except ValueError:
        return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _session(row: Row) -> SessionRow:
    return SessionRow(
        id=str(row["id"]),
        role=str(row["role"]),
        state=str(row["state"]),
        tunnel_id=row["tunnel_id"],
        peer_device=row["peer_device"],
        token_expires=row["token_expires"],
        chosen=_chosen(row["chosen"]),
        offered_files=int(row["offered_files"]),
        wanted_files=int(row["wanted_files"]),
        sent_files=int(row["sent_files"]),
        sent_bytes=int(row["sent_bytes"]),
        rate_bps=row["rate_bps"],
        dest_folder_id=row["dest_folder_id"],
        started_at=int(row["started_at"]),
        ended_at=row["ended_at"],
        end_reason=row["end_reason"],
        cut_off_at=row["cut_off_at"],
        two_way=bool(row["two_way"]),
        back_offered=int(row["back_offered"]),
        back_wanted=int(row["back_wanted"]),
        back_files=int(row["back_files"]),
        back_bytes=int(row["back_bytes"]),
    )


def _done(text: object) -> tuple[int, ...]:
    try:
        value = json.loads(str(text or "[]"))
    except ValueError:
        return ()
    if not isinstance(value, list):
        return ()
    return tuple(sorted({int(one) for one in value if isinstance(one, int) and one >= 0}))


def _manifest(row: Row) -> ManifestRow:
    return ManifestRow(
        session_id=str(row["session_id"]),
        file_key=str(row["file_key"]),
        size=int(row["size"]),
        chunk_size=int(row["chunk_size"]),
        done=_done(row["done"]),
        digest=row["digest"],
        staged_path=row["staged_path"],
        updated_at=int(row["updated_at"]),
        version=row["version"],
    )


class SessionStore:
    """The device row, the session rows and the manifests. Every write is one statement.

    **What a screen draws says so when it moves.** The device row is Settings > Privacy > Swaps'
    device id, so its two writers tell every admin the settings moved; a session's row is what the
    swap screen and Activity's row draw (the state a waiting host is watching for, the counts, the
    rate), so each of its writers tells every admin the work moved (`About.JOBS`, the subject the
    session's own task already answers to). `telling` says nothing when no row changed, and the bus
    folds a second's worth into one message, so a writer called per chunk costs one re-read a
    second at most. The manifests are drawn by no screen and say nothing.
    """

    def __init__(self, database: Database) -> None:
        self._db = database

    @property
    def database(self) -> Database:
        return self._db

    # --- the device ------------------------------------------------------------------------------

    async def device(self) -> DeviceRow | None:
        row = await self._db.fetch_one(_DEVICE)
        if row is None:
            return None
        return DeviceRow(str(row["device_id"]), str(row["key_secret_id"]), int(row["created_at"]))

    async def put_device_if_absent(self, device_id: str, key_secret_id: str, now: int) -> bool:
        """Write the device row unless one is there. Whether this call's row is the one kept."""
        async with telling(self._db, EVERY_ADMIN, About.SETTINGS) as connection:
            await connection.execute(_INSERT_DEVICE, (device_id, key_secret_id, now))
        kept = await self.device()
        return kept is not None and kept.key_secret_id == key_secret_id

    async def replace_device(self, device_id: str, key_secret_id: str, now: int) -> None:
        async with telling(self._db, EVERY_ADMIN, About.SETTINGS) as connection:
            await connection.execute(_REPLACE_DEVICE, (device_id, key_secret_id, now))

    # --- sessions --------------------------------------------------------------------------------

    async def create(
        self,
        session_id: str,
        *,
        role: str,
        started_at: int,
        started_by: str,
        tunnel_id: str | None = None,
        peer_device: str | None = None,
        token_expires: int | None = None,
        chosen: list[dict[str, object]] | None = None,
        dest_folder_id: str | None = None,
        two_way: bool = False,
    ) -> None:
        """The row, and the ledger's line that somebody started or joined a swap, in one
        transaction, so History never knows of a swap the table does not. `started_by` is the
        user who pressed; `peer_device` is the host's id a guest already holds from the token."""
        async with telling(self._db, EVERY_ADMIN, About.JOBS) as connection:
            await connection.execute(
                _INSERT_SESSION,
                (
                    session_id,
                    role,
                    tunnel_id,
                    peer_device,
                    token_expires,
                    json.dumps(chosen or []),
                    dest_folder_id,
                    started_at,
                    int(two_way),
                ),
            )
            payload: dict[str, object] = (
                {"role": role} if peer_device is None else {"role": role, "device": peer_device}
            )
            if two_way:
                # An exchange: History says so ("You started an exchange"). Only the side that
                # pressed Start knows it here; a guest learns it from the host's hello, after this.
                payload["two_way"] = True
            await record_event(
                connection,
                actor=Actor.user(started_by),
                verb="swap_started",
                subject=Subject(kind="swap", id=session_id),
                payload=json.dumps(payload),
            )

    async def get(self, session_id: str) -> SessionRow | None:
        row = await self._db.fetch_one(_SESSION, (session_id,))
        return None if row is None else _session(row)

    async def move(self, session_id: str, state: str, *, peer_device: str | None = None) -> bool:
        """Move a live session to a live state. False when the row has already ended."""
        if state not in LIVE_STATES:
            raise ValueError(f"{state!r} is not a live state; a session ends through end()")
        async with telling(self._db, EVERY_ADMIN, About.JOBS) as connection:
            cursor = await connection.execute(_MOVE, (state, peer_device, session_id))
            return (cursor.rowcount or 0) > 0

    async def set_offered(self, session_id: str, files: int, *, back: bool = False) -> None:
        """How many files were offered: from the host, or with `back` from the guest."""
        async with telling(self._db, EVERY_ADMIN, About.JOBS) as connection:
            await connection.execute(
                _SET_BACK_OFFERED if back else _SET_OFFERED, (max(0, files), session_id)
            )

    async def set_wanted(self, session_id: str, files: int, *, back: bool = False) -> None:
        """How many of them were wanted: by the guest, or with `back` by the host."""
        async with telling(self._db, EVERY_ADMIN, About.JOBS) as connection:
            await connection.execute(
                _SET_BACK_WANTED if back else _SET_WANTED, (max(0, files), session_id)
            )

    async def add_sent(
        self, session_id: str, *, files: int = 0, sent_bytes: int = 0, back: bool = False
    ) -> None:
        """Count files and bytes moved from the host to the guest (on a guest's row "sent" means
        received), or with `back` from the guest to the host."""
        async with telling(self._db, EVERY_ADMIN, About.JOBS) as connection:
            await connection.execute(
                _ADD_BACK if back else _ADD_SENT, (max(0, files), max(0, sent_bytes), session_id)
            )

    async def set_two_way(self, session_id: str) -> None:
        """The host's hello said files go both ways: the guest's row says so too."""
        async with telling(self._db, EVERY_ADMIN, About.JOBS) as connection:
            await connection.execute(_SET_TWO_WAY, (session_id,))

    async def set_rate(self, session_id: str, rate_bps: int) -> None:
        async with telling(self._db, EVERY_ADMIN, About.JOBS) as connection:
            await connection.execute(_SET_RATE, (max(0, rate_bps), session_id))

    async def cut_off(self, session_id: str, now: int) -> bool:
        """A tunnel cut the session off: it stays in its step, and says so. False when it had
        ended or was cut off already."""
        async with telling(self._db, EVERY_ADMIN, About.JOBS) as connection:
            cursor = await connection.execute(_CUT_OFF, (now, session_id))
            return (cursor.rowcount or 0) > 0

    async def rejoined(self, session_id: str) -> bool:
        """The same token joined the session again: it carries on from its step."""
        async with telling(self._db, EVERY_ADMIN, About.JOBS) as connection:
            cursor = await connection.execute(_REJOINED, (session_id,))
            return (cursor.rowcount or 0) > 0

    async def end(self, session_id: str, *, state: str, reason: str, now: int) -> bool:
        """End a live session. False when it had already ended: the first end is the one kept."""
        if state not in TERMINAL_STATES:
            raise ValueError(f"{state!r} is not a state a session ends in")
        async with telling(self._db, EVERY_ADMIN, About.JOBS) as connection:
            cursor = await connection.execute(_END, (state, reason, now, session_id))
            if not (cursor.rowcount or 0):
                return False
            # The ledger's line, in the same transaction as the end it describes: Sift's act (the
            # session ended, whoever pressed), with the side, the other device, why and how much.
            facts = next(iter(await connection.execute_fetchall(_ENDED_FACTS, (session_id,))))
            ended: dict[str, object] = {
                "role": str(facts["role"]),
                "device": facts["peer_device"],
                "reason": reason,
                "files": int(facts["sent_files"] or 0),
            }
            if facts["two_way"]:
                # The other direction's files, from the guest to the host, beside the first.
                ended["back"] = int(facts["back_files"] or 0)
            await record_event(
                connection,
                actor=Actor.sift(VIA_SWAP),
                verb="swap_ended",
                subject=Subject(kind="swap", id=session_id),
                payload=json.dumps(ended),
            )
            return True

    async def unfinished(self) -> list[str]:
        """Every session still in a live state, oldest first."""
        return [str(row["id"]) for row in await self._db.fetch_all(_UNFINISHED)]

    # --- manifests -------------------------------------------------------------------------------

    async def manifest(self, session_id: str, file_key: str) -> ManifestRow | None:
        row = await self._db.fetch_one(_MANIFEST, (session_id, file_key))
        return None if row is None else _manifest(row)

    async def put_manifest(
        self,
        session_id: str,
        file_key: str,
        *,
        size: int,
        chunk_size: int,
        digest: str | None,
        staged_path: str,
        now: int,
        version: str | None = None,
    ) -> ManifestRow:
        """Start a file's manifest afresh: nothing verified yet. It names its bytes by `digest`
        or, from a sender that checks the whole at the end, by `version`."""
        await self._db.execute(
            _PUT_MANIFEST,
            (session_id, file_key, size, chunk_size, digest, version, staged_path, now),
        )
        kept = await self.manifest(session_id, file_key)
        if kept is None:  # pragma: no cover (the statement above wrote it)
            raise RuntimeError("the manifest was not written")
        return kept

    async def adoptable(
        self,
        session_id: str,
        *,
        peer_device: str,
        file_key: str,
        size: int,
        chunk_size: int,
        digest: str | None,
        version: str | None = None,
    ) -> ManifestRow | None:
        """An earlier session's unfinished manifest of the same bytes from the same device."""
        row = await self._db.fetch_one(
            _ADOPTABLE, (peer_device, file_key, size, chunk_size, digest, version, session_id)
        )
        return None if row is None else _manifest(row)

    async def adopt(self, found: ManifestRow, session_id: str, now: int) -> ManifestRow:
        """Move an earlier session's manifest onto this one, chunks and staging with it."""
        await self._db.execute(_MOVE_MANIFEST, (session_id, now, found.session_id, found.file_key))
        kept = await self.manifest(session_id, found.file_key)
        if kept is None:  # pragma: no cover (the statement above moved it)
            raise RuntimeError("the manifest was not moved")
        return kept

    async def mark_done(self, session_id: str, file_key: str, chunk: int, now: int) -> None:
        """Append one verified chunk to the file's manifest, once."""
        await self._db.execute(_MARK_DONE, (chunk, now, session_id, file_key, chunk))

    async def drop_manifest(self, session_id: str, file_key: str) -> None:
        await self._db.execute(_DROP_MANIFEST, (session_id, file_key))

    async def stale_manifests(self, before: int, *, limit: int = 500) -> list[ManifestRow]:
        """Manifests nobody has touched since `before`, oldest first."""
        rows = await self._db.fetch_all(_STALE_MANIFESTS, (before, limit))
        return [_manifest(row) for row in rows]


# ================================================================================================
# THE LANDING'S ROWS (the receiving side: what a file that arrived counts, and who sent it)
# ================================================================================================
#
# Module functions rather than methods on `SessionStore`, because the landing writes its count
# INSIDE the transaction that files the file and records its arrival (`ingest._file_it`): a
# session row that says 38 received while 37 files carry the event, or the other way round, is the
# disagreement one transaction exists to prevent. A file that does not land writes nothing here: the
# session counts it as failed, in memory, for the life of the session it belongs to.
#
# On the guest's row `sent_files` is the files RECEIVED: the column is named from the host's side.
# The landing is its only writer on the guest's side, and the bytes (`sent_bytes`) are the
# transfer's, counted as the pieces arrive. In a swap that sends and receives the host receives
# too, and its landing counts `back_files` the same way.

# A file lands on the side receiving it: the guest's from the host (`sent_files`), and in a swap
# that sends and receives, the host's from the guest (`back_files`, see `schema.py`).
_COUNT_LANDED = (
    "UPDATE swap_sessions SET sent_files = sent_files + (role = 'guest'),"
    " back_files = back_files + (role = 'host') WHERE id = ?"
)
# The person a guest's diff matched, as this library calls them now, or nothing, when they were
# deleted while the swap ran. Read inside the landing's own transaction.
_PERSON_NAME = "SELECT name FROM people WHERE id = ?"
# A session by the short id History carries: the last eight characters of its ULID, which are its
# random part. Two rows are read so that two sessions sharing those eight characters (one chance
# in a trillion) answer nothing rather than the wrong device.
_PEER_BY_SHORT_ID = (
    "SELECT peer_device FROM swap_sessions WHERE substr(id, -8) = ? AND peer_device IS NOT NULL"
    " ORDER BY id DESC LIMIT 2"
)


async def count_landed_on(connection: Connection, session_id: str) -> None:
    """One more file received, on the landing's own connection."""
    await connection.execute(_COUNT_LANDED, (session_id,))


async def person_name_on(connection: Connection, person_id: str) -> str | None:
    """What this library calls a person now, or None when they are not here any more."""
    rows = list(await connection.execute_fetchall(_PERSON_NAME, (person_id,)))
    return str(rows[0]["name"]) if rows else None


async def peer_of(database: Database, short_id: str) -> str | None:
    """The other install's device id for the session History calls `short_id`, or None.

    The `added` event of a file that arrived by swap carries the device itself; this is for a line
    that has only the session to go on.
    """
    rows = await database.fetch_all(_PEER_BY_SHORT_ID, (short_id,))
    return str(rows[0]["peer_device"]) if len(rows) == 1 else None


# ================================================================================================
# THE OFFER'S AND THE DIFF'S READERS (people's other names and stash-box ids)
# ================================================================================================
#
# Reads of the kernel's people tables, never of a file: which files a swap offers is decided by the
# scoped read in `offer.py`, and whether a person may be named by the scoped read of people. These
# answer only about people somebody has already been allowed to see, by id: the aliases and the
# stash-box ids an offer carries, and, on the guest, which of its people a stash-box id names.

#: A person's other names, in the order their page lists them.
_ALIASES_OF_PEOPLE = (
    "SELECT person_id, alias FROM people_aliases"
    " WHERE person_id IN (SELECT value FROM json_each(?))"
    " ORDER BY person_id, COALESCE(alias_sort, alias), id"
)

#: The stash-box ids held for each of these people, one per box they are linked on.
_BOX_IDS_OF_PEOPLE = (
    "SELECT person_id, remote_id FROM person_stash_box_links"
    " WHERE person_id IN (SELECT value FROM json_each(?)) AND remote_id <> ''"
    " ORDER BY person_id, box_id"
)

#: Who here each of these stash-box ids names.
_PEOPLE_BY_BOX = (
    "SELECT remote_id, person_id FROM person_stash_box_links"
    " WHERE remote_id IN (SELECT value FROM json_each(?))"
    " ORDER BY remote_id, person_id"
)


async def aliases_of_people(database: Database, person_ids: list[str]) -> dict[str, list[str]]:
    """Each of these people's aliases, keyed by person. A person with none is absent."""
    found: dict[str, list[str]] = {}
    if not person_ids:
        return found
    for row in await database.fetch_all(_ALIASES_OF_PEOPLE, (json.dumps(person_ids),)):
        found.setdefault(str(row["person_id"]), []).append(str(row["alias"]))
    return found


async def box_ids_of_people(database: Database, person_ids: list[str]) -> dict[str, list[str]]:
    """Each of these people's stash-box ids, keyed by person. A person linked nowhere is absent."""
    found: dict[str, list[str]] = {}
    if not person_ids:
        return found
    for row in await database.fetch_all(_BOX_IDS_OF_PEOPLE, (json.dumps(person_ids),)):
        found.setdefault(str(row["person_id"]), []).append(str(row["remote_id"]))
    return found


async def people_by_box(database: Database, remote_ids: list[str]) -> dict[str, set[str]]:
    """Which of this device's people each stash-box id names, keyed by the id. Unscoped: the caller
    passes every answer through the scoped read of people before anything is shown."""
    wanted = [one for one in dict.fromkeys(remote_ids) if one]
    found: dict[str, set[str]] = {}
    if not wanted:
        return found
    for row in await database.fetch_all(_PEOPLE_BY_BOX, (json.dumps(wanted),)):
        found.setdefault(str(row["remote_id"]), set()).add(str(row["person_id"]))
    return found
