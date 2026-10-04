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
import math
import struct
from collections.abc import Sequence
from dataclasses import dataclass

from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce
from sift.kernel.db import Database, in_clause
from sift.kernel.forgetting import register_forgetting
from sift.kernel.log import get_logger
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

#: How many neighbours to ask for beyond what the caller wants.
#:
#: The lookup runs before any permission rule does: it is arithmetic over numbers, and it knows
#: nothing about who is asking, so some of what it returns will be filtered out afterwards, and
#: several frames of one file collapse into one result. Asking for exactly a page of neighbours
#: therefore returns less than a page. This is the margin, and the caller is told when it was not
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
_EXISTS = "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'semantic_frames'"
# A batch per write, so removing the index never holds the one writer for minutes. The frames by
# rowid, one at a time: a vec0 table answers a rowid lookup, and an IN over a scan of it would not.
_SOME_FRAMES = "SELECT rowid FROM semantic_frames LIMIT ?"
_CLEAR_FRAME = "DELETE FROM semantic_frames WHERE rowid = ?"
_CLEAR_POOLED = (
    "DELETE FROM semantic_pooled WHERE rowid IN (SELECT rowid FROM semantic_pooled LIMIT ?)"
)
#: Rows one write of the removal takes.
CLEAR_BATCH = 500
_NEAREST = (
    "SELECT asset_id, at_ms, distance FROM semantic_frames "
    "WHERE embedding MATCH ? AND k = ? AND revision = ? ORDER BY distance"
)


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


def _pooled(vectors: Sequence[Sequence[float]]) -> list[float]:
    """One set of numbers standing for a whole file: the average of its frames, back to length one.

    The ONE place that rule is written. It is applied twice (once where the frames are stored, to
    keep the pooled row, and once where an old file's pooled row is filled in on the way past) and
    two copies of it would be two answers to "what does this file look like" that drift apart
    silently, because neither is ever compared with the other.

    Empty for no frames, and empty for frames that cancel out exactly: there is no direction to
    scale, and a zero vector would compare as equally near everything.
    """
    if not vectors:
        return []
    total = [0.0] * DIMENSION
    for vector in vectors:
        for index, value in enumerate(vector):
            total[index] += value
    length = math.sqrt(sum(value * value for value in total))
    if length == 0.0:
        return []
    return [value / length for value in total]


def _pack(vector: Sequence[float]) -> bytes:
    """A vector as the store wants it: plain little-endian floats, no framing.

    Its own text form exists and is JSON, which for seven hundred and sixty-eight numbers is around
    six times the bytes and a parse on both sides of every write.
    """
    return struct.pack(f"{len(vector)}f", *vector)


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
            await connection.execute(_FORGET, (asset_id,))
            await connection.execute(_FORGET_POOLED, (asset_id,))
            for at_ms, vector in frames:
                if len(vector) != DIMENSION:
                    raise ValueError(
                        f"a frame description has {len(vector)} numbers, and this index holds "
                        f"{DIMENSION}"
                    )
                await connection.execute(_INSERT, (revision, asset_id, at_ms, _pack(vector)))
            # The whole-file description, from the numbers already in hand. Nothing is read to
            # build it, and it saves the scan `describes` would otherwise be. See `_KEEP_POOLED`.
            pooled = _pooled([vector for _, vector in frames])
            if pooled:
                await connection.execute(_KEEP_POOLED, (asset_id, revision, _pack(pooled)))

    async def forget(self, asset_id: str) -> None:
        """Drop everything held about one file. Safe when there is nothing."""
        if not self.available or not await self.built():
            return
        async with self._database.write() as connection:
            await connection.execute(_FORGET, (asset_id,))
            await connection.execute(_FORGET_POOLED, (asset_id,))

    async def nearest(
        self, vector: Sequence[float], *, revision: str, limit: int
    ) -> list[Neighbour]:
        """The closest frames to this one, nearest first, at most one per file.

        Among the frames `revision` described and no others: the question is that model's
        numbers, and another model's are the same length and mean nothing to it. A file the
        previous model described is simply not in the answer until it has been described again.

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
        rows = await self._database.fetch_all(_NEAREST, (_pack(vector), wanted, revision))

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
                await connection.execute(_KEEP_POOLED, (asset_id, revision, _pack(pooled)))
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
                await connection.execute(_FORGET, (asset_id,))
                # And the pooled reading of those frames, which has no key reaching it either:
                # deliberately, so that it goes at the same moment they do. See the schema.
                await connection.execute(_FORGET_POOLED, (asset_id,))
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
        while True:
            async with self._database.write() as connection:
                cursor = await connection.execute(_CLEAR_POOLED, (CLEAR_BATCH,))
            if not cursor.rowcount:
                break
            await asyncio.sleep(0)
        while True:
            async with self._database.write() as connection:
                rows = list(await connection.execute_fetchall(_SOME_FRAMES, (CLEAR_BATCH,)))
                await connection.executemany(_CLEAR_FRAME, [(int(row[0]),) for row in rows])
                if not rows:
                    # Settings > Smart Search draws the index's size; an open pane re-reads on this.
                    announce(EVERY_ADMIN, About.SETTINGS)
            if not rows:
                break
            await asyncio.sleep(0)
        log.info("semantic.index.cleared")

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
                dropped += held[one]
        log.info("semantic.index.purged", revisions=len(stale), frames=dropped)
        return dropped


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

    #: The vectors and the pooled reading of them. `semantic_indexed` beside them is an ordinary
    #: table with a real foreign key, so it goes on its own and naming it here would claim work
    #: nothing does; `semantic_pooled` has no key on purpose (see the schema), so it is swept
    #: here, with the frames it is an average of.
    tables = ("semantic_frames", "semantic_pooled")

    def __init__(self, database: Database) -> None:
        self._database = database

    async def forget(self, asset_ids: Sequence[str]) -> int:
        return await VectorStore(self._database).prune(list(asset_ids))


register_forgetting(_ForgetFromVectors.name, _ForgetFromVectors.tables, _ForgetFromVectors)
