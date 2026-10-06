# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where the numbers describing pictures are kept, and how the nearest ones are found.

**The seam is deliberately narrow, and the reason is a version number.** The extension behind this
is not yet at 1.0, and it is load-bearing for the whole feature, so everything specific to it is
inside this file: its table type, its idea of what a vector looks like on the wire, the shape of a
nearest-neighbour query. Outside, there are four verbs (put some frames, forget a file, find the
nearest, say how many there are) and nothing that would have to change if the thing underneath
were replaced. That is what makes a swap a bounded job rather than a rewrite.

**One vector per sampled frame, not one per file.** The inference cost is identical either way:
every sampled frame has to be described regardless, and pooling them into one is an average taken
afterwards. What differs is what can be asked. Thirty frames at this width is about ninety
kilobytes against three, and keeping them apart is what makes "jump to the moment" possible at all;
averaging at index time throws that away permanently and there is no way back without re-reading
every file.

**The layout is flat.** Grouping each file's frames together reads as the natural choice and is a
trap: the store reserves a whole block per group, so two thousand files of thirty frames each
reserve two thousand blocks sized for a thousand vectors apiece. On sixty thousand vectors:
**6.3 GB grouped against 189 MB flat**, and a search of 902 ms against 48. Same numbers, same
query, same extension. The file each vector came from is an ordinary column here, and the grouping
happens when results are read.

**Nothing here is created at boot.** The table type arrives with the extension, the extension can
be absent, and Sift has to start anyway, so the table is made the first time something genuinely
needs it, and `available` is the honest answer everywhere else.
"""

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

#: The name this feature's extension is registered under. Read back off the database to find out
#: whether it actually loaded on this machine.
EXTENSION = "vec"

#: How many numbers describe one frame. Baked into the table when it is made, because the store
#: needs to know the width to lay rows out, so a model of a different width is not a setting, it
#: is a rebuild, and the schema version is what forces one.
DIMENSION = 768

# **Every vector stored here is scaled to length one, and the ordering depends on it.**
#
# The store measures straight-line distance. The models are compared by ANGLE (which of two
# pictures points more nearly the same way as a description) and those are different measures.
# They agree, exactly, but only for vectors of equal length: for two unit vectors the straight-line
# distance and the angle between them rise and fall together, so sorting by one sorts by the other.
#
# Feed this an unscaled vector and nothing errors. Results simply start being ordered partly by how
# BRIGHT or busy a picture is, because that is what changes a vector's length: a wrong answer with
# no symptom. So the scaling happens where the numbers are made, in the embedder, and the pooled
# description of a whole file is scaled again after averaging for the same reason.

#: How many frames to ask for beyond the files the caller wants.
#:
#: Several frames of one file collapse into one result, so asking for exactly a page of frames
#: returns less than a page of files. This is the margin, and the caller is told when it was not
#: enough rather than being handed a short page that looks complete.
OVERSAMPLE = 8

#: The most frames one nearest-neighbour lookup may ask for.
#:
#: The vector index refuses a larger `k` outright (it is a hard limit in the extension, not a
#: tuning knob) and the refusal arrives as a database error in the middle of a search. Asking for
#: a page far enough into a result set is enough to cross it: the caller widens its ask when a page
#: is still short, that ask is multiplied by the margin above, and the product can pass the limit
#: while the page it is trying to fill is perfectly ordinary. So the ask is clamped here, at the
#: one place that knows what the index will take.
K_LIMIT = 4096

#: The most files one lookup can name, once the margin is taken off. What a caller may usefully ask
#: for: beyond this the clamp above would silently give back the same answer for a bigger number.
MAX_NEIGHBOURS = K_LIMIT // OVERSAMPLE

# Written out in full rather than assembled around `DIMENSION`, and the width appears twice as a
# result. That is deliberate: no SQL in Sift is built by string formatting: it is the only
# injection control there is, and a rule that holds except where the value looked harmless is not a
# rule. The two cannot drift apart, because a test asserts they agree.
#
# **`revision` is a partition key, and it is what keeps a search inside one model's numbers.** The
# record of which model described a file is beside the file, in the records table; vectors that
# carried nothing, under a nearest-neighbour query that filtered by nothing, would after a model
# change rank the old model's numbers against the new model's question until the whole library
# had been described again, and nothing would say so. A partition key narrows the scan to the
# chunks of one revision, which costs the model in use nothing extra and the ones that are not
# in use nothing at all.
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

# One pooled vector per file, the index "like this file" ranks first: a quarter of the frames' rows,
# and no frame read. Its rowid is the file's key in `semantic_file_keys`.
_CREATE_FILES = """
CREATE VIRTUAL TABLE IF NOT EXISTS semantic_files USING vec0(
  revision  TEXT partition key,
  embedding float[768]
)
"""
_INSERT_FILE = "INSERT INTO semantic_files(revision, embedding) VALUES (?, ?)"
_FORGET_FILE = "DELETE FROM semantic_files WHERE rowid = ?"
_FORGET_FILES_OF_REVISION = "DELETE FROM semantic_files WHERE revision = ?"

# Each vector's file kept by key in an ordinary table, so a viewer's scope is an index join; the
# file id inside the virtual table is text read row by row.
_KEEP_FRAME_KEY = "INSERT INTO semantic_frame_keys (frame, asset_id, revision) VALUES (?, ?, ?)"
_FORGET_FRAME_KEYS = "DELETE FROM semantic_frame_keys WHERE asset_id = ?"
_FORGET_FRAME_KEYS_OF_REVISION = "DELETE FROM semantic_frame_keys WHERE revision = ?"
_KEEP_FILE_KEY = "INSERT INTO semantic_file_keys (file, asset_id, revision) VALUES (?, ?, ?)"
_FILE_KEY_OF = "SELECT file FROM semantic_file_keys WHERE asset_id = ?"
_FORGET_FILE_KEY = "DELETE FROM semantic_file_keys WHERE asset_id = ?"
_FORGET_FILE_KEYS_OF_REVISION = "DELETE FROM semantic_file_keys WHERE revision = ?"

# One file's whole description, kept in an ordinary table beside the frames. See
# `slices/semantic/schema.py` for why it exists and what it costs. Written where the frames are
# written, out of the same numbers, and read by primary key.
_KEEP_POOLED = (
    "INSERT INTO semantic_pooled (asset_id, revision, pooled) VALUES (?, ?, ?) "
    "ON CONFLICT(asset_id, revision) DO UPDATE SET pooled = excluded.pooled"
)
_READ_POOLED = "SELECT pooled FROM semantic_pooled WHERE asset_id = ? AND revision = ?"
# Many files' pooled rows in one read, and only where the RECORD says the model in use described
# the file: the same authority `SemanticService._frames_of` asks one file at a time.
_READ_POOLED_MANY = (
    "SELECT p.asset_id AS asset_id, p.pooled AS pooled FROM semantic_pooled p"
    " JOIN semantic_indexed i ON i.asset_id = p.asset_id AND i.revision = p.revision"
    " WHERE p.revision = ? AND p.asset_id IN (?*)"
)
_DESCRIBED_MANY = "SELECT asset_id FROM semantic_indexed WHERE revision = ? AND asset_id IN (?*)"
_FORGET_POOLED = "DELETE FROM semantic_pooled WHERE asset_id = ?"

# Every revision the frames hold, with how many rows each has. What the purge is surveyed with: a
# model change leaves the previous model's numbers behind for every file that has not been
# described again, and nothing in a search will ever return one of them.
_REVISIONS_HELD = "SELECT revision, COUNT(*) AS total FROM semantic_frames GROUP BY revision"
_FORGET_REVISION = "DELETE FROM semantic_frames WHERE revision = ?"
_FORGET_POOLED_REVISION = "DELETE FROM semantic_pooled WHERE revision = ?"

#: Every file this index holds vectors for.
#:
#: Read so they can be checked against the library, because nothing tells this table when a file is
#: deleted: the record of WHICH files have been described is an ordinary table with a foreign key
#: and cleans itself up, and the vectors cannot: they live in a virtual table, and SQLite takes no
#: foreign key on one. So a deleted file leaves its frames behind, invisible, never returned by any
#: search, and growing.
#:
#: Deliberately NOT joined against the assets table here. That table carries permissions, and a
#: query against it written inside a feature is a second opinion about what a file is: there is a
#: gate that refuses one. The ids go to the kernel, which answers which of them still exist.
_HELD_IDS = "SELECT DISTINCT asset_id FROM semantic_frames"
_COUNT = "SELECT COUNT(*) AS total FROM semantic_frames"
_FRAMES_OF = "SELECT embedding FROM semantic_frames WHERE asset_id = ? AND revision = ?"
# The files' table, made after the frames' in the same write: holding it means holding both.
_EXISTS = "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'semantic_files'"
# A batch per write, so removing the index never holds the one writer for minutes. The frames by
# rowid, one at a time: a vec0 table answers a rowid lookup, and an IN over a scan of it would not.
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
#: Rows one write of the removal takes.
CLEAR_BATCH = 500
_NEAREST = (
    "SELECT asset_id, at_ms, distance FROM semantic_frames "
    "WHERE embedding MATCH ? AND k = ? AND revision = ? ORDER BY distance"
)
# Only the asker's own files are ranked, so a hidden file can neither fill the page nor shorten it.
# By rowid, from the keys: the index tests a rowid IN by search.
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

#: Writes to the index in this process: what a kept ranking is kept under (`index_writes`).
_WRITES = [0]


def index_writes() -> int:
    """How many times this process has written to the index. Moves on every write, and only then."""
    return _WRITES[0]


def _moved() -> None:
    _WRITES[0] += 1


class VectorStoreUnavailable(RuntimeError):
    """The vector store cannot be used on this machine. The message says why, in words."""


@dataclass(frozen=True, slots=True)
class Neighbour:
    """One file the numbers point at, and the moment in it that matched.

    The moment is what makes this worth keeping per frame: a result is not "this video is somewhat
    like your query", it is "at four minutes twelve, this video looks like your query".
    """

    asset_id: str
    at_ms: int
    distance: float


def _unpack(raw: bytes) -> tuple[float, ...]:
    """The other direction, for the one question that reads numbers back rather than comparing
    them: what does this file look like, so that things like it can be found."""
    return struct.unpack(f"{len(raw) // 4}f", raw)


def _pooled(vectors: Sequence[Sequence[float]] | npt.NDArray[np.float32]) -> list[float]:
    """One set of numbers standing for a whole file: the average of its frames, back to length one.

    The ONE place that rule is written. It is applied twice (once where the frames are stored, to
    keep the pooled row, and once where an old file's pooled row is filled in on the way past) and
    two copies of it would be two answers to "what does this file look like" that drift apart
    silently, because neither is ever compared with the other.

    Empty for no frames, and empty for frames that cancel out exactly: there is no direction to
    scale, and a zero vector would compare as equally near everything.
    """
    if len(vectors) == 0:
        return []
    total = np.asarray(vectors, dtype=np.float64).sum(axis=0)
    length = float(np.linalg.norm(total))
    if length == 0.0:
        return []
    pooled: list[float] = (total / length).tolist()
    return pooled


def _pack(vector: Sequence[float]) -> bytes:
    """A vector as the store wants it: plain little-endian floats, no framing.

    Its own text form exists and is JSON, which for seven hundred and sixty-eight numbers is around
    six times the bytes and a parse on both sides of every write.
    """
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
    """The nearest-neighbour index over frame descriptions. One per database.

    Every method is safe to call on a machine that cannot load the extension: they raise
    `VectorStoreUnavailable` with something a person can read, and nothing they do is partial.
    """

    def __init__(self, database: Database) -> None:
        self._database = database
        self._ready = False

    @property
    def available(self) -> bool:
        """Whether the extension this is built on loaded when the database was opened.

        Read off the connection rather than probed again: the answer was settled at boot and
        cannot change while the process runs.
        """
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
        """Make the table if it is not there yet. Called before the first WRITE, and only then.

        Never from a read. The search box asks whether this install can search by meaning on every
        page it draws, and that question runs through here, so a read that built the table would
        create it on every install that merely CAN load the add-on, whether or not anybody ever
        switched the feature on. Which is exactly the thing this feature is careful not to do.
        """
        self.require()
        if self._ready or await self.built():
            return
        async with self._database.write() as connection:
            await connection.execute(_CREATE)
            await connection.execute(_CREATE_FILES)
        self._ready = True

    async def built(self) -> bool:
        """Whether the table exists yet, asked without making one.

        Cached once true, because a table is never dropped while the process runs, and asked again
        while false, because the pass that builds it may start at any moment.
        """
        if self._ready:
            return True
        if await self._database.fetch_one(_EXISTS) is None:
            return False
        self._ready = True
        return True

    async def put(
        self, asset_id: str, frames: Sequence[tuple[int, Sequence[float]]], *, revision: str
    ) -> None:
        """Replace everything held about one file with these frames, in one transaction.

        Replace rather than add: describing a file again (because the model changed, or because a
        pass was interrupted halfway) must not leave the earlier numbers beside the new ones. Two
        descriptions of the same moment would both be returned, and the older one is from a model
        whose numbers are not comparable with anything.

        `revision` is the model that produced these numbers, stamped on every row. It is what a
        search keeps to; see `_CREATE`.
        """
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
            # The whole-file description, from the numbers already in hand. Nothing is read to
            # build it, and it saves the scan `describes` would otherwise be. See `_KEEP_POOLED`.
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
        """The closest frames to this one, nearest first, at most one per file, among the files
        `asker` may see; every file for a pass with no asker.

        Among `revision`'s frames only: another model's numbers mean nothing to this one.

        Collapsed here rather than by the caller: thirty frames of one video are thirty
        neighbours, and a page of results made of the same file thirty times is not a page of
        results. The frame kept is the closest one, which is also the moment worth jumping to.
        """
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
        """The files whose whole description sits nearest these numbers, nearest first, among the
        files `asker` may see; every file for a pass with no asker.

        One pooled vector per file, so a file is ranked as a file and no frame is read: the
        question "what looks like this file" asks it."""
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
        """One set of numbers standing for a whole file, or empty when it has none.

        The average of its frames, scaled back to unit length. A file is not one picture and its
        frames are kept apart for exactly that reason, but "find me things like this file" is a
        question about the file, so the frames are pooled *here*, at the moment of asking, rather
        than at index time where pooling would throw the individual moments away for good.

        Empty for a file only another model has described: what comes back is about to be
        compared with `revision`'s numbers, and the other model's would compare as noise.

        **Read from the pooled table, and the frames only when it has no row yet.** The frames are
        in a virtual table, which takes no index of any kind (`CREATE INDEX` on one is refused
        outright), so asking it about one file walks the whole of that model's partition: hundreds
        of milliseconds on a large library, the same whether the file has frames or none. Every file described since the pooled table arrived has a row; one described before it
        does not, and pays that scan ONCE and is written down on the way past.

        That write is on a read path and it is deliberate, the same way the table's own
        carry-forward is: it stores nothing that was not already stored, the arithmetic is fixed,
        and the alternative is reading every frame in a migration (tens of seconds) at the boot
        of an upgrade.
        """
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
        """Many files' descriptions at once, keyed by file; a file with none is absent.

        Two reads by key rather than one per file: the pooled rows the record vouches for, then
        which files the record says are described and had no pooled row: those were described
        before the pooled table existed, and each pays `describes`' one-time scan and is written
        down on the way past, exactly as one asked alone would be.
        """
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
        if not self.available or not await self.built():
            return 0
        row = await self._database.fetch_one(_COUNT)
        return int(row["total"]) if row is not None else 0

    async def held_ids(self) -> list[str]:
        """Every file this index holds vectors for. For the prune. See `_HELD_IDS`."""
        if not self.available or not await self.built():
            return []
        rows = await self._database.sweep_all(_HELD_IDS, what="described files")
        return [str(row["asset_id"]) for row in rows]

    async def prune(self, gone: Sequence[str]) -> int:
        """Drop the vectors of these files. Returns how many were dropped.

        Which files have gone is decided by the caller, from the kernel's answer, because working it
        out here would mean querying the assets table from a feature. See `_HELD_IDS`.

        Counted by FILE, which is the number worth logging: one video is dozens of rows, and
        "removed 5,000 frames" says nothing about how much was thrown away.
        """
        if not gone or not self.available or not await self.built():
            return 0
        async with self._database.write() as connection:
            for asset_id in gone:
                # The frames, and the pooled reading and the keys of them, which no foreign key
                # reaches either: deliberately, so that they go at the same moment. See the schema.
                await _forget_in(connection, asset_id)
        _moved()
        log.info("semantic.index.pruned", files=len(gone))
        return len(gone)

    async def clear(self) -> None:
        """Throw the whole index away. What the "remove the index" control does.

        Switching the feature off does NOT do this: turning something off to see what it does
        should not cost hours of re-reading every file, so it is a separate, deliberate act.

        The pooled descriptions go with the frames, and they have to: a pooled row left behind
        would keep answering "this is what that file looks like" out of an index that no longer
        holds a single one of the frames it was averaged from.
        """
        if not self.available or not await self.built():
            return
        await self._delete_in_batches(_CLEAR_POOLED)
        await self._forget_in_batches(_SOME_FILES, _FORGET_FILE)
        await self._forget_in_batches(_SOME_FRAMES, _CLEAR_FRAME)
        # Settings > Smart Search draws the index's size; an open pane re-reads on this. Every
        # batch has committed, so it is told now rather than after a write.
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
        """Which models' numbers are in the index, and how many frames each has.

        The survey behind the purge below. Cheap enough to ask on a settings screen: it is a
        grouped count over the frames and returns one row per model, which on every install that
        has never changed model is one row.
        """
        if not self.available or not await self.built():
            return {}
        # A whole-table read, so it goes through the sweep block rather than the ordinary pool.
        async with self._database.sweep("revisions held") as connection:
            rows = await connection.execute_fetchall(_REVISIONS_HELD, ())
        return {str(row["revision"]): int(row["total"]) for row in rows}

    async def purge_other_revisions(self, revision: str) -> int:
        """Drop every frame described by a model other than this one. Returns how many went.

        **Nothing in a search can reach them.** Every read here is filtered to one model's
        revision, because numbers from two models are the same length and mean nothing to each
        other, so the previous model's frames stopped being an answer the moment the model
        changed, and they stay for ever, because a file is only re-described when the sweep reaches
        it and a file that is never reached is never replaced.

        What it costs is the one thing worth saying out loud: going BACK to the previous model
        would then mean describing the whole library with it again. That is why this is a press and
        not something a sweep does on its own. See `slices/semantic/tidy.py`.
        """
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
# In frame order, so one file's frames, written in one transaction, arrive together.
_KEYS_AFTER = (
    "SELECT frame, asset_id, revision FROM semantic_frame_keys"
    " WHERE frame > ? ORDER BY frame LIMIT ?"
)
_ONE_FRAME = "SELECT embedding FROM semantic_frames WHERE rowid = ?"

# The add-on's own storage, read only here: a vector read through the table walks its chunk from
# the start, half a millisecond a row, and a chunk read whole is a thousand rows at once. Trusted
# only where the layout is the one expected and a vector read both ways agrees.
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
# Files whose frames are not one run of keys: another file's frames lie between theirs.
_SPLIT_FILES = (
    "SELECT asset_id, revision FROM semantic_frame_keys k GROUP BY asset_id, revision"
    " HAVING COUNT(*) != (SELECT COUNT(*) FROM semantic_frame_keys r"
    " WHERE r.frame BETWEEN MIN(k.frame) AND MAX(k.frame))"
)
_FRAMES_OF_FILE = (
    "SELECT frame FROM semantic_frame_keys WHERE asset_id = ? AND revision = ? ORDER BY frame"
)
#: Frame keys read, and files written, per statement of the step below.
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
    """Key every frame by its file and put every file in the files' index, pooling the files
    described before the pooled table existed; for the schema step that adds the keys.

    Nothing to do where no frame was ever stored. Where the add-on is not loaded here the frames
    cannot be read, so the record of what was described is cleared and the files are described
    again once it loads.
    """
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


#: The vector table. Published so an operator can be told the add-on did not load, and why.
STORE: Part[VectorStore] = Part("semantic_store")


# --- what the end of an asset takes out of here ------------------------------------------------


class _ForgetFromVectors:
    """Clearing deleted files out of the vector index.

    `semantic_frames` is a `vec0` virtual table, so SQLite hangs no foreign key on it and nothing
    cascades: a deleted file leaves its frames behind, invisible, never returned by any search, and
    growing: easily thousands of files' worth, which is not a rounding error.

    **The sweep's prune is not enough on its own**, which is the reason this exists rather than
    a wider schedule. The sweep prunes, but the sweep runs only when the feature is switched ON and
    ready, and it is only ever asked for when files ARRIVE. Switch the feature off, or delete a file
    and import nothing afterwards, and the frames stay for good. The sweep is still the catch-up for
    anything this misses; what it cannot be is the only thing that acts.

    Safe on an install that cannot use the store at all: `prune` answers zero when the add-on did
    not load or the table was never made, which is the ordinary state on most machines rather than
    a failure.
    """

    name = "vector-index"

    #: The vectors, the pooled reading of them and their keys. `semantic_indexed` beside them is an
    #: ordinary table with a real foreign key, so it goes on its own and naming it here would claim
    #: work nothing does; `semantic_pooled` and the keys have no key on purpose (see the schema),
    #: so they are swept here, with the frames.
    tables = ("semantic_frames", "semantic_pooled", "semantic_frame_keys", "semantic_file_keys")

    def __init__(self, database: Database) -> None:
        self._database = database

    async def forget(self, asset_ids: Sequence[str]) -> int:
        return await VectorStore(self._database).prune(list(asset_ids))


register_forgetting(_ForgetFromVectors.name, _ForgetFromVectors.tables, _ForgetFromVectors)
