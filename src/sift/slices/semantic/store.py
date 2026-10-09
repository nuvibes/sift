# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where frame vectors are kept and the nearest found, behind a narrow seam."""

from __future__ import annotations

import asyncio
import struct
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from sift.kernel.access.ranked import VIEWER_FILES, ranked_among
from sift.kernel.access.viewer import Viewer
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce_now
from sift.kernel.db import Connection, Database, in_clause
from sift.kernel.forgetting import register_forgetting
from sift.kernel.log import get_logger
from sift.kernel.sql_splice import splice
from sift.kernel.wiring import Part

log = get_logger(__name__)

#: Read back off the database to learn whether the extension loaded here.
EXTENSION = "vec"

#: Baked into the table, so a model of another width is a rebuild, forced by the schema version.
DIMENSION = 768

# Every vector here has unit length: only then does straight-line distance order by angle.

#: Several frames of one file collapse into one result, so a page of frames is short of files.
OVERSAMPLE = 8

#: The index refuses a larger `k` outright, mid-search, so the ask is clamped here.
K_LIMIT = 4096

#: Beyond this the clamp would give the same answer for a bigger number.
MAX_NEIGHBOURS = K_LIMIT // OVERSAMPLE

# Written out in full, never formatted; a test holds the width to `DIMENSION`.
_CREATE = """
CREATE VIRTUAL TABLE IF NOT EXISTS semantic_frames USING vec0(
  revision  TEXT partition key,
  asset_id  TEXT,
  at_ms     INTEGER,
  embedding float[768]
)
"""

_INSERT = "INSERT INTO semantic_frames(revision, asset_id, at_ms, embedding) VALUES (?, ?, ?, ?)"

_FORGET = "DELETE FROM semantic_frames WHERE asset_id = ?"

# One pooled vector per file, ranked first for "like this file"; its rowid is the file's key.
_CREATE_FILES = """
CREATE VIRTUAL TABLE IF NOT EXISTS semantic_files USING vec0(
  revision  TEXT partition key,
  embedding float[768]
)
"""
_INSERT_FILE = "INSERT INTO semantic_files(revision, embedding) VALUES (?, ?)"
_FORGET_FILE = "DELETE FROM semantic_files WHERE rowid = ?"
_FORGET_FILES_OF_REVISION = "DELETE FROM semantic_files WHERE revision = ?"

# Each vector's file by key in an ordinary table, so a viewer's scope is an index join.
_KEEP_FRAME_KEY = "INSERT INTO semantic_frame_keys (frame, asset_id, revision) VALUES (?, ?, ?)"
_FORGET_FRAME_KEYS = "DELETE FROM semantic_frame_keys WHERE asset_id = ?"
_FORGET_FRAME_KEYS_OF_REVISION = "DELETE FROM semantic_frame_keys WHERE revision = ?"
_KEEP_FILE_KEY = "INSERT INTO semantic_file_keys (file, asset_id, revision) VALUES (?, ?, ?)"
_FILE_KEY_OF = "SELECT file FROM semantic_file_keys WHERE asset_id = ?"
_FORGET_FILE_KEY = "DELETE FROM semantic_file_keys WHERE asset_id = ?"
_FORGET_FILE_KEYS_OF_REVISION = "DELETE FROM semantic_file_keys WHERE revision = ?"

# One file's whole description beside its frames, written with them, read by primary key.
_KEEP_POOLED = (
    "INSERT INTO semantic_pooled (asset_id, revision, pooled) VALUES (?, ?, ?) "
    "ON CONFLICT(asset_id, revision) DO UPDATE SET pooled = excluded.pooled"
)
_READ_POOLED = "SELECT pooled FROM semantic_pooled WHERE asset_id = ? AND revision = ?"
# Many files' pooled rows in one read, only where the record says the model in use did them.
_READ_POOLED_MANY = (
    "SELECT p.asset_id AS asset_id, p.pooled AS pooled FROM semantic_pooled p"
    " JOIN semantic_indexed i ON i.asset_id = p.asset_id AND i.revision = p.revision"
    " WHERE p.revision = ? AND p.asset_id IN (?*)"
)
_DESCRIBED_MANY = "SELECT asset_id FROM semantic_indexed WHERE revision = ? AND asset_id IN (?*)"
_FORGET_POOLED = "DELETE FROM semantic_pooled WHERE asset_id = ?"

# Every revision the frames hold, with its row count, for the purge's survey.
_REVISIONS_HELD = "SELECT revision, COUNT(*) AS total FROM semantic_frames GROUP BY revision"
_FORGET_REVISION = "DELETE FROM semantic_frames WHERE revision = ?"
_FORGET_POOLED_REVISION = "DELETE FROM semantic_pooled WHERE revision = ?"

#: Files with vectors and no record; the kernel says which of them are gone.
_UNRECORDED_IDS = "SELECT asset_id FROM semantic_unrecorded"
_COUNT = "SELECT COALESCE(SUM(frames), 0) AS total FROM semantic_counts"
_FRAMES_OF = "SELECT embedding FROM semantic_frames WHERE asset_id = ? AND revision = ?"
# Made after the frames' table in the same write: holding it means holding both.
_EXISTS = "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'semantic_files'"
# A batch per write; frames by rowid one at a time, since a vec0 IN over a scan would not use it.
_SOME_FRAMES = "SELECT rowid FROM semantic_frames LIMIT ?"
_CLEAR_FRAME = "DELETE FROM semantic_frames WHERE rowid = ?"
_CLEAR_POOLED = (
    "DELETE FROM semantic_pooled WHERE rowid IN (SELECT rowid FROM semantic_pooled LIMIT ?)"
)
_SOME_FILES = "SELECT rowid FROM semantic_files LIMIT ?"
# Last, after the vectors they name: a key left by a stop names nothing and is harmless.
_CLEAR_KEYS = (
    "DELETE FROM semantic_file_keys WHERE file IN (SELECT file FROM semantic_file_keys LIMIT ?)",
    "DELETE FROM semantic_frame_keys WHERE frame IN (SELECT frame FROM semantic_frame_keys LIMIT ?)",
)
CLEAR_BATCH = 500
_NEAREST = (
    "SELECT asset_id, at_ms, distance FROM semantic_frames "
    "WHERE embedding MATCH ? AND k = ? AND revision = ? ORDER BY distance"
)
# Only the asker's own files are ranked, so a hidden file can neither fill nor shorten the page.
_NEAREST_AMONG = splice(
    "SELECT asset_id, at_ms, distance FROM semantic_frames"
    " WHERE embedding MATCH :vector AND k = :k AND revision = :revision"
    " AND rowid IN (SELECT fk.frame FROM semantic_frame_keys fk"
    " WHERE fk.asset_id IN ({{VIEWER_FILES}})) ORDER BY distance",
    VIEWER_FILES=VIEWER_FILES,
)
_NEAREST_FILES = (
    "WITH near AS (SELECT rowid, distance FROM semantic_files"
    " WHERE embedding MATCH :vector AND k = :k AND revision = :revision)"
    " SELECT fk.asset_id AS asset_id, near.distance AS distance FROM near"
    " JOIN semantic_file_keys fk ON fk.file = near.rowid ORDER BY near.distance"
)
_NEAREST_FILES_AMONG = splice(
    "WITH near AS (SELECT rowid, distance FROM semantic_files"
    " WHERE embedding MATCH :vector AND k = :k AND revision = :revision"
    " AND rowid IN (SELECT fk.file FROM semantic_file_keys fk"
    " WHERE fk.asset_id IN ({{VIEWER_FILES}})))"
    " SELECT fk.asset_id AS asset_id, near.distance AS distance FROM near"
    " JOIN semantic_file_keys fk ON fk.file = near.rowid ORDER BY near.distance",
    VIEWER_FILES=VIEWER_FILES,
)

_WRITES = [0]


def index_writes() -> int:
    """How many times this process has written to the index; moves on every write, and only then."""
    return _WRITES[0]


def _moved() -> None:
    _WRITES[0] += 1


class VectorStoreUnavailable(RuntimeError):
    """The vector store cannot be used on this machine. The message says why, in words."""


@dataclass(frozen=True, slots=True)
class Neighbour:
    """One file the numbers point at, and the moment in it that matched."""

    asset_id: str
    at_ms: int
    distance: float


def _unpack(raw: bytes) -> tuple[float, ...]:
    """The numbers read back for one file, so things like it can be found."""
    return struct.unpack(f"{len(raw) // 4}f", raw)


def _pooled(vectors: Sequence[Sequence[float]] | npt.NDArray[np.float32]) -> list[float]:
    """A whole file's numbers: its frames averaged back to unit length, the one copy of the rule."""
    if len(vectors) == 0:
        return []
    total = np.asarray(vectors, dtype=np.float64).sum(axis=0)
    length = float(np.linalg.norm(total))
    if length == 0.0:
        return []
    pooled: list[float] = (total / length).tolist()
    return pooled


def _pack(vector: Sequence[float]) -> bytes:
    """A vector as the store wants it: plain little-endian floats, no framing."""
    return struct.pack(f"{len(vector)}f", *vector)


async def _forget_file(connection: Connection, asset_id: str) -> None:
    """Take one file out of the files' index, by its key."""
    for row in await connection.execute_fetchall(_FILE_KEY_OF, (asset_id,)):
        await connection.execute(_FORGET_FILE, (int(row[0]),))
    await connection.execute(_FORGET_FILE_KEY, (asset_id,))


async def _forget_in(connection: Connection, asset_id: str) -> None:
    """Everything held about one file, inside the caller's write."""
    await connection.execute(_FORGET, (asset_id,))
    await connection.execute(_FORGET_FRAME_KEYS, (asset_id,))
    await connection.execute(_FORGET_POOLED, (asset_id,))
    await _forget_file(connection, asset_id)


async def _keep_file(connection: Connection, asset_id: str, revision: str, pooled: bytes) -> None:
    """One file's pooled description, kept and put in the files' index in the caller's write."""
    await connection.execute(_KEEP_POOLED, (asset_id, revision, pooled))
    await _forget_file(connection, asset_id)
    cursor = await connection.execute(_INSERT_FILE, (revision, pooled))
    await connection.execute(_KEEP_FILE_KEY, (cursor.lastrowid, asset_id, revision))


class VectorStore:
    """The nearest-neighbour index over frame descriptions, safe without the extension."""

    def __init__(self, database: Database) -> None:
        self._database = database
        self._ready = False

    @property
    def available(self) -> bool:
        """Whether the extension loaded when the database was opened; settled at boot."""
        return EXTENSION in self._database.extensions

    def require(self) -> None:
        """Raise unless the store can be used, with the reason a person needs to act on it."""
        if not self.available:
            raise VectorStoreUnavailable(
                "Search by meaning needs an add-on that this machine's copy of SQLite cannot "
                "load. Everything else in Sift works without it. The container image ships a "
                "copy that can, so running Sift in its container is the usual answer."
            )

    async def ensure_ready(self) -> None:
        """Make the table before the first write, never from a read, so a search never builds it."""
        self.require()
        if self._ready or await self.built():
            return
        async with self._database.write() as connection:
            await connection.execute(_CREATE)
            await connection.execute(_CREATE_FILES)
        self._ready = True

    async def built(self) -> bool:
        """Whether the table exists yet, asked without making one; cached once true."""
        if self._ready:
            return True
        if await self._database.fetch_one(_EXISTS) is None:
            return False
        self._ready = True
        return True

    async def put(
        self, asset_id: str, frames: Sequence[tuple[int, Sequence[float]]], *, revision: str
    ) -> None:
        """Replace everything held about one file with these frames, in one transaction."""
        await self.ensure_ready()
        async with self._database.write() as connection:
            await _forget_in(connection, asset_id)
            for at_ms, vector in frames:
                if len(vector) != DIMENSION:
                    raise ValueError(
                        f"a frame description has {len(vector)} numbers, and this index holds "
                        f"{DIMENSION}"
                    )
                cursor = await connection.execute(
                    _INSERT, (revision, asset_id, at_ms, _pack(vector))
                )
                await connection.execute(_KEEP_FRAME_KEY, (cursor.lastrowid, asset_id, revision))
            # The whole-file description from the numbers in hand, sparing `describes` its scan.
            pooled = _pooled([vector for _, vector in frames])
            if pooled:
                await _keep_file(connection, asset_id, revision, _pack(pooled))
        # After the commit: a ranking taken while the write was open is kept under the old count.
        _moved()

    async def forget(self, asset_id: str) -> None:
        """Drop everything held about one file. Safe when there is nothing."""
        if not self.available or not await self.built():
            return
        async with self._database.write() as connection:
            await _forget_in(connection, asset_id)
        _moved()

    async def nearest(
        self,
        vector: Sequence[float],
        *,
        revision: str,
        limit: int,
        asker: Viewer | None = None,
    ) -> list[Neighbour]:
        """The closest frames, one per file, among `revision`'s and `asker`'s only."""
        self.require()
        if not await self.built():
            # Nothing has been described yet, so there is nothing near anything. An empty answer,
            # not a table brought into existence by somebody searching.
            return []
        wanted = min(limit * OVERSAMPLE, K_LIMIT)
        among = None if asker is None else await ranked_among(self._database, asker)
        if among is None:
            rows = await self._database.fetch_all(_NEAREST, (_pack(vector), wanted, revision))
        else:
            asked = {"vector": _pack(vector), "k": wanted, "revision": revision, **among}
            rows = await self._database.fetch_all(_NEAREST_AMONG, asked)

        best: dict[str, Neighbour] = {}
        for row in rows:
            asset_id = str(row["asset_id"])
            if asset_id in best:
                # Rows arrive nearest first, so the first sighting of a file is its closest frame.
                continue
            best[asset_id] = Neighbour(
                asset_id=asset_id, at_ms=int(row["at_ms"]), distance=float(row["distance"])
            )
            if len(best) == limit:
                break
        return list(best.values())

    async def nearest_files(
        self,
        vector: Sequence[float],
        *,
        revision: str,
        limit: int,
        asker: Viewer | None = None,
    ) -> tuple[tuple[str, float], ...]:
        """The files whose whole description sits nearest these numbers, among `asker`'s files."""
        self.require()
        if not await self.built():
            return ()
        asked = {"vector": _pack(vector), "k": min(limit, K_LIMIT), "revision": revision}
        among = None if asker is None else await ranked_among(self._database, asker)
        if among is None:
            rows = await self._database.fetch_all(_NEAREST_FILES, asked)
        else:
            rows = await self._database.fetch_all(_NEAREST_FILES_AMONG, {**asked, **among})
        return tuple((str(row["asset_id"]), float(row["distance"])) for row in rows)

    async def describes(self, asset_id: str, *, revision: str) -> list[float]:
        """One set of numbers for a whole file, or empty; pooled, else from its frames once."""
        if not self.available or not await self.built():
            return []
        held = await self._database.fetch_one(_READ_POOLED, (asset_id, revision))
        if held is not None:
            return list(_unpack(held["pooled"]))
        rows = await self._database.fetch_all(_FRAMES_OF, (asset_id, revision))
        if not rows:
            return []
        pooled = _pooled([_unpack(row["embedding"]) for row in rows])
        if pooled:
            async with self._database.write() as connection:
                await _keep_file(connection, asset_id, revision, _pack(pooled))
            _moved()
        return pooled

    async def describes_many(
        self, asset_ids: Sequence[str], *, revision: str
    ) -> dict[str, list[float]]:
        """Many files' descriptions in one read, keyed by file; a file with none is absent."""
        if not asset_ids or not self.available or not await self.built():
            return {}
        wanted = sorted(set(asset_ids))
        asked, values = in_clause(_READ_POOLED_MANY, wanted)
        found = {
            str(row["asset_id"]): list(_unpack(row["pooled"]))
            for row in await self._database.fetch_all(asked, [revision, *values])
        }
        asked, values = in_clause(_DESCRIBED_MANY, wanted)
        for row in await self._database.fetch_all(asked, [revision, *values]):
            asset_id = str(row["asset_id"])
            if asset_id not in found:
                pooled = await self.describes(asset_id, revision=revision)
                if pooled:
                    found[asset_id] = pooled
        return found

    async def count(self) -> int:
        """How many frames are described. Zero when the store cannot be used at all."""
        if not self.available:
            return 0
        row = await self._database.fetch_one(_COUNT)
        return int(row["total"]) if row is not None else 0

    async def held_ids(self) -> list[str]:
        """The files held with no record, among them every file that has left. See
        `_UNRECORDED_IDS`."""
        if not self.available:
            return []
        rows = await self._database.fetch_all(_UNRECORDED_IDS)
        return [str(row["asset_id"]) for row in rows]

    async def prune(self, gone: Sequence[str]) -> int:
        """Drop the vectors of these files, which the caller found gone; returns how many files."""
        if not gone or not self.available or not await self.built():
            return 0
        async with self._database.write() as connection:
            for asset_id in gone:
                # The frames, pooled reading and keys go together: no foreign key reaches any.
                await _forget_in(connection, asset_id)
        _moved()
        log.info("semantic.index.pruned", files=len(gone))
        return len(gone)

    async def clear(self) -> None:
        """Throw the whole index away, pooled rows included; switching off does not do this."""
        if not self.available or not await self.built():
            return
        await self._delete_in_batches(_CLEAR_POOLED)
        await self._forget_in_batches(_SOME_FILES, _FORGET_FILE)
        await self._forget_in_batches(_SOME_FRAMES, _CLEAR_FRAME)
        # Settings > Smart Search draws the index's size; told now, every batch committed.
        announce_now(EVERY_ADMIN, About.SETTINGS)
        for clear_keys in _CLEAR_KEYS:
            await self._delete_in_batches(clear_keys)
        _moved()
        log.info("semantic.index.cleared")

    async def _delete_in_batches(self, statement: str) -> None:
        """A bounded delete at a time, the loop between each, until none is left."""
        while True:
            async with self._database.write() as connection:
                cursor = await connection.execute(statement, (CLEAR_BATCH,))
            if not cursor.rowcount:
                return
            await asyncio.sleep(0)

    async def _forget_in_batches(self, some: str, forget: str) -> None:
        """A batch of rowids read, then forgotten one by one, until none is left."""
        while True:
            async with self._database.write() as connection:
                rows = list(await connection.execute_fetchall(some, (CLEAR_BATCH,)))
                await connection.executemany(forget, [(int(row[0]),) for row in rows])
            if not rows:
                return
            await asyncio.sleep(0)

    async def revisions_held(self) -> dict[str, int]:
        """Which models' numbers are in the index, and how many frames each has."""
        if not self.available or not await self.built():
            return {}
        # A whole-table read, so it goes through the sweep block rather than the ordinary pool.
        async with self._database.sweep("revisions held") as connection:
            rows = await connection.execute_fetchall(_REVISIONS_HELD, ())
        return {str(row["revision"]): int(row["total"]) for row in rows}

    async def purge_other_revisions(self, revision: str) -> int:
        """Drop every frame from another model, which no search can reach; returns how many."""
        if not self.available or not await self.built():
            return 0
        dropped = 0
        held = await self.revisions_held()
        stale = [one for one in held if one != revision]
        if not stale:
            return 0
        async with self._database.write() as connection:
            for one in stale:
                await connection.execute(_FORGET_REVISION, (one,))
                await connection.execute(_FORGET_POOLED_REVISION, (one,))
                await connection.execute(_FORGET_FILES_OF_REVISION, (one,))
                await connection.execute(_FORGET_FILE_KEYS_OF_REVISION, (one,))
                await connection.execute(_FORGET_FRAME_KEYS_OF_REVISION, (one,))
                dropped += held[one]
        _moved()
        log.info("semantic.index.purged", revisions=len(stale), frames=dropped)
        return dropped


# --- bringing an index made before the keys forward ---------------------------------------------

_HOLDS_FRAMES = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'semantic_frames'"
_ADD_ON_HERE = "SELECT 1 FROM pragma_function_list WHERE name = 'vec_version'"
_DESCRIBE_AGAIN = "DELETE FROM semantic_indexed"
_KEY_EVERY_FRAME = (
    "INSERT INTO semantic_frame_keys (frame, asset_id, revision)"
    " SELECT rowid, asset_id, revision FROM semantic_frames"
)
# In frame order, so one file's frames arrive together.
_KEYS_AFTER = (
    "SELECT frame, asset_id, revision FROM semantic_frame_keys"
    " WHERE frame > ? ORDER BY frame LIMIT ?"
)
_ONE_FRAME = "SELECT embedding FROM semantic_frames WHERE rowid = ?"

# The add-on's own storage, read by chunk where the layout is known and agrees with the table.
_LAYOUT = (
    "SELECT"
    " (SELECT COUNT(*) FROM pragma_table_info('semantic_frames_rowids')"
    "  WHERE name IN ('chunk_id', 'chunk_offset')) = 2"
    " AND (SELECT COUNT(*) FROM pragma_table_info('semantic_frames_vector_chunks00')"
    "  WHERE name = 'vectors') = 1"
)
_SLOTS = (
    "SELECT rowid, chunk_id, chunk_offset FROM semantic_frames_rowids WHERE rowid BETWEEN ? AND ?"
)
_CHUNK = "SELECT vectors FROM semantic_frames_vector_chunks00 WHERE rowid = ?"
_POOLED_OF = "SELECT asset_id, revision, pooled FROM semantic_pooled WHERE asset_id IN (?*)"
_LAST_FILE = "SELECT COALESCE(MAX(file), 0) FROM semantic_file_keys"
_INSERT_FILE_AT = "INSERT INTO semantic_files(rowid, revision, embedding) VALUES (?, ?, ?)"
# Files whose frames are not one run of keys.
_SPLIT_FILES = (
    "SELECT asset_id, revision FROM semantic_frame_keys k GROUP BY asset_id, revision"
    " HAVING COUNT(*) != (SELECT COUNT(*) FROM semantic_frame_keys r"
    " WHERE r.frame BETWEEN MIN(k.frame) AND MAX(k.frame))"
)
_FRAMES_OF_FILE = (
    "SELECT frame FROM semantic_frame_keys WHERE asset_id = ? AND revision = ? ORDER BY frame"
)
INDEX_BATCH = 5000


class _Frames:
    """Frames read by key for the step below, a chunk at a time where the layout allows."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self._chunked = False
        self._slots: dict[int, tuple[int, int]] = {}
        self._chunk: tuple[int, bytes] | None = None

    async def settle(self, sample: int) -> None:
        """Read by chunk only where the layout is known and agrees with the table on `sample`."""
        (row,) = await self._connection.execute_fetchall(_LAYOUT, ())
        if not row[0]:
            return
        by_table = await self.read([sample])
        self._chunked = True
        await self.between(sample, sample)
        self._chunked = await self.read([sample]) == by_table

    async def between(self, first: int, last: int) -> None:
        """Where the frames keyed `first` to `last` sit, read in one statement."""
        if self._chunked:
            rows = await self._connection.execute_fetchall(_SLOTS, (first, last))
            self._slots = {int(row[0]): (int(row[1]), int(row[2])) for row in rows}

    async def read(self, frames: Sequence[int]) -> list[bytes]:
        found: list[bytes] = []
        width = DIMENSION * 4
        for frame in frames:
            if not self._chunked:
                rows = await self._connection.execute_fetchall(_ONE_FRAME, (frame,))
                found.extend(bytes(row[0]) for row in rows)
                continue
            chunk, slot = self._slots[frame]
            if self._chunk is None or self._chunk[0] != chunk:
                (held,) = await self._connection.execute_fetchall(_CHUNK, (chunk,))
                self._chunk = (chunk, bytes(held[0]))
            found.append(self._chunk[1][slot * width : (slot + 1) * width])
        return found


async def index_files(connection: Connection) -> None:
    """Key every frame by its file and fill the files' index, for the step adding the keys."""
    if not await connection.execute_fetchall(_HOLDS_FRAMES, ()):
        return
    if not await connection.execute_fetchall(_ADD_ON_HERE, ()):
        await connection.execute(_DESCRIBE_AGAIN)
        return
    await connection.execute(_CREATE_FILES)
    await connection.execute(_KEY_EVERY_FRAME)
    split = {
        (str(row[0]), str(row[1])) for row in await connection.execute_fetchall(_SPLIT_FILES, ())
    }
    frames = _Frames(connection)
    files: list[tuple[str, str, list[int]]] = []
    after = 0
    while rows := list(await connection.execute_fetchall(_KEYS_AFTER, (after, INDEX_BATCH))):
        if after == 0:
            await frames.settle(int(rows[0][0]))
        for row in rows:
            frame, asset_id, revision = int(row[0]), str(row[1]), str(row[2])
            if (asset_id, revision) in split:
                continue
            if files and files[-1][:2] == (asset_id, revision):
                files[-1][2].append(frame)
            else:
                files.append((asset_id, revision, [frame]))
        after = int(rows[-1][0])
        # The last file's frames may go on in the next batch.
        await _index_many(connection, frames, files[:-1])
        files = files[-1:]
    await _index_many(connection, frames, files)
    for asset_id, revision in sorted(split):
        keys = await connection.execute_fetchall(_FRAMES_OF_FILE, (asset_id, revision))
        await _index_many(connection, frames, [(asset_id, revision, [int(row[0]) for row in keys])])


async def _index_many(
    connection: Connection, frames: _Frames, files: Sequence[tuple[str, str, list[int]]]
) -> None:
    if not files:
        return
    await frames.between(files[0][2][0], files[-1][2][-1])
    asked, values = in_clause(_POOLED_OF, sorted({asset_id for asset_id, _, _ in files}))
    held = {
        (str(row[0]), str(row[1])): bytes(row[2])
        for row in await connection.execute_fetchall(asked, values)
    }
    ((last,),) = await connection.execute_fetchall(_LAST_FILE, ())
    pooled_rows: list[tuple[str, str, bytes]] = []
    file_rows: list[tuple[int, str, bytes]] = []
    key_rows: list[tuple[int, str, str]] = []
    for asset_id, revision, keys in files:
        pooled = held.get((asset_id, revision))
        if pooled is None:
            raw = b"".join(await frames.read(keys))
            made = _pooled(np.frombuffer(raw, dtype=np.float32).reshape(-1, DIMENSION))
            if not made:
                continue
            pooled = _pack(made)
            pooled_rows.append((asset_id, revision, pooled))
        last += 1
        file_rows.append((last, revision, pooled))
        key_rows.append((last, asset_id, revision))
    await connection.executemany(_KEEP_POOLED, pooled_rows)
    await connection.executemany(_INSERT_FILE_AT, file_rows)
    await connection.executemany(_KEEP_FILE_KEY, key_rows)


STORE: Part[VectorStore] = Part("semantic_store")


# --- what the end of an asset takes out of here ------------------------------------------------


class _ForgetFromVectors:
    """Clearing deleted files out of the vector index, which nothing cascades into."""

    name = "vector-index"

    #: The vectors, their pooled reading and their keys; `semantic_indexed` has a real foreign key.
    tables = ("semantic_frames", "semantic_pooled", "semantic_frame_keys", "semantic_file_keys")

    def __init__(self, database: Database) -> None:
        self._database = database

    async def forget(self, asset_ids: Sequence[str]) -> int:
        return await VectorStore(self._database).prune(list(asset_ids))


register_forgetting(_ForgetFromVectors.name, _ForgetFromVectors.tables, _ForgetFromVectors)
