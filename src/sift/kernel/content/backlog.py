# SPDX-License-Identifier: AGPL-3.0-or-later
"""The passes' counts kept as rows: each count a term over the files, its total per media kind.

A write that can move a file into or out of a term marks the file in `backlog_moved`, in the
write's own transaction, by the triggers `marks` writes on every table a term reads. A read folds
in the files marked since that term was last read, so a count costs the files that moved rather
than the library, and is exact whenever it is read. A term is built
from the library the first time it is asked, and again when a check finds it disagreeing.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import structlog

from sift.kernel.db import Connection, Database

log = structlog.get_logger(__name__)

#: How many terms are kept: one bit each of a file's mask. The least recently asked gives way.
TERMS_KEPT = 62

#: How often a kept count is walked again and compared, which is what bounds a missed mark.
CHECK_EVERY_SECONDS = 6 * 3600

#: Files evaluated per statement when folding, and per write when building.
_FOLD_AT_ONCE = 500
_BUILD_AT_ONCE = 500

_CREATE_CLOCK = """
CREATE TABLE IF NOT EXISTS backlog_clock (
  id  INTEGER PRIMARY KEY CHECK (id = 1),
  seq INTEGER NOT NULL
)
"""

_SEED_CLOCK = "INSERT OR IGNORE INTO backlog_clock (id, seq) VALUES (1, 0)"

# No foreign key on either: a file that has left is marked and folded out like any other.
_CREATE_MOVED = """
CREATE TABLE IF NOT EXISTS backlog_moved (
  asset_id TEXT PRIMARY KEY,
  seq      INTEGER NOT NULL
) WITHOUT ROWID
"""

_CREATE_TERMS = """
CREATE TABLE IF NOT EXISTS backlog_terms (
  bit        INTEGER PRIMARY KEY CHECK (bit BETWEEN 0 AND 61),
  signature  TEXT NOT NULL UNIQUE,
  folded_to  INTEGER NOT NULL,
  asked_at   INTEGER NOT NULL,
  checked_at INTEGER NOT NULL,
  building   INTEGER NOT NULL DEFAULT 0
)
"""

_CREATE_FILES = """
CREATE TABLE IF NOT EXISTS backlog_files (
  asset_id TEXT PRIMARY KEY,
  kind     TEXT NOT NULL,
  mask     INTEGER NOT NULL
) WITHOUT ROWID
"""

_CREATE_COUNTS = """
CREATE TABLE IF NOT EXISTS backlog_counts (
  bit  INTEGER NOT NULL,
  kind TEXT NOT NULL,
  n    INTEGER NOT NULL,
  PRIMARY KEY (bit, kind)
) WITHOUT ROWID
"""

TABLES = (
    _CREATE_CLOCK,
    _SEED_CLOCK,
    _CREATE_MOVED,
    "CREATE INDEX IF NOT EXISTS ix_backlog_moved_seq ON backlog_moved(seq)",
    _CREATE_TERMS,
    _CREATE_FILES,
    _CREATE_COUNTS,
)

# A delete then an insert, never OR REPLACE: a trigger's conflict clause gives way to the one on
# the statement that fired it, and an upsert or OR IGNORE there would fail or drop the mark.
_MARK = (
    "UPDATE backlog_clock SET seq = seq + 1 WHERE id = 1;"
    " DELETE FROM backlog_moved WHERE asset_id = <<ID>>;"
    " INSERT INTO backlog_moved (asset_id, seq)"
    " VALUES (<<ID>>, (SELECT seq FROM backlog_clock WHERE id = 1));"
)


def marks(table: str, column: str = "asset_id", *, watched: Sequence[str] = ()) -> tuple[str, ...]:
    """The triggers that mark a file whenever a row of `table` naming it is written. `watched`
    narrows an update to those columns, and to a value that changed; none is every update."""
    named = f"backlog_{table}"
    changed = " OR ".join(f"OLD.{one} IS NOT NEW.{one}" for one in watched)
    on_update = f"AFTER UPDATE ON {table}"
    if watched:
        on_update = f"AFTER UPDATE OF {', '.join(watched)} ON {table} WHEN {changed}"
    old, new = _MARK.replace("<<ID>>", f"OLD.{column}"), _MARK.replace("<<ID>>", f"NEW.{column}")
    return (
        f"CREATE TRIGGER IF NOT EXISTS {named}_added AFTER INSERT ON {table} BEGIN {new} END",
        f"CREATE TRIGGER IF NOT EXISTS {named}_removed AFTER DELETE ON {table} BEGIN {old} END",
        f"CREATE TRIGGER IF NOT EXISTS {named}_moved {on_update} BEGIN {old} {new} END",
    )


@dataclass(frozen=True, slots=True)
class Term:
    """One count: a condition over the assets row `a`, its values bound in order."""

    condition: str
    params: tuple[Any, ...] = ()

    @property
    def signature(self) -> str:
        text = json.dumps([self.condition, list(self.params)], default=str)
        return hashlib.sha256(text.encode()).hexdigest()


_NOW = "SELECT seq FROM backlog_clock WHERE id = 1"

_TERMS_OF = (
    "SELECT bit, signature, folded_to, checked_at, building FROM backlog_terms"
    " WHERE signature IN (SELECT value FROM json_each(?))"
)

_BITS_TAKEN = "SELECT bit FROM backlog_terms ORDER BY asked_at, bit"

_NEW_TERM = (
    "INSERT INTO backlog_terms (bit, signature, folded_to, asked_at, checked_at, building)"
    " VALUES (?, ?, ?, ?, ?, 1)"
)

_BUILT = "UPDATE backlog_terms SET building = 0 WHERE bit = ?"

_FORGET_TERM = "DELETE FROM backlog_terms WHERE bit = ?"

_FORGET_COUNTS = "DELETE FROM backlog_counts WHERE bit = ?"

_CLEAR_BIT = "UPDATE backlog_files SET mask = mask & ~(1 << ?) WHERE (mask & (1 << ?)) <> 0"

_DROP_EMPTY = "DELETE FROM backlog_files WHERE mask = 0"

_MOVED_SINCE = "SELECT asset_id FROM backlog_moved WHERE seq > ? ORDER BY seq"

_FILES_OF = (
    "SELECT asset_id, kind, mask FROM backlog_files"
    " WHERE asset_id IN (SELECT value FROM json_each(?))"
)

_KEEP_FILE = (
    "INSERT INTO backlog_files (asset_id, kind, mask) VALUES (?, ?, ?)"
    " ON CONFLICT(asset_id) DO UPDATE SET kind = excluded.kind, mask = excluded.mask"
)

_DROP_FILE = "DELETE FROM backlog_files WHERE asset_id = ?"

_ADD_COUNT = (
    "INSERT INTO backlog_counts (bit, kind, n) VALUES (?, ?, ?)"
    " ON CONFLICT(bit, kind) DO UPDATE SET n = n + excluded.n"
)

_COUNTS_OF = (
    "SELECT bit, kind, n FROM backlog_counts"
    " WHERE bit IN (SELECT value FROM json_each(?)) AND n <> 0"
)

_FOLDED = (
    "UPDATE backlog_terms SET folded_to = ?, asked_at = ?"
    " WHERE bit IN (SELECT value FROM json_each(?))"
)

_CHECKED = "UPDATE backlog_terms SET checked_at = ? WHERE bit = ?"

_PRUNE = "DELETE FROM backlog_moved WHERE seq <= (SELECT MIN(folded_to) FROM backlog_terms)"

#: Each term's value for some files; `<<TERMS>>` is `(condition) AS tN`, the constants only.
_EVALUATE = (
    "SELECT a.id AS id, a.media_type AS kind, <<TERMS>> FROM assets a"
    " WHERE a.id IN (SELECT value FROM json_each(?))"
)

_EVALUATE_AFTER = (
    "SELECT a.id AS id, a.media_type AS kind, <<TERMS>> FROM assets a"
    " WHERE a.id > ? ORDER BY a.id LIMIT ?"
)

#: The check: the same terms walked over every file, by kind.
_WALK = "SELECT a.media_type AS kind, <<SUMS>> FROM assets a GROUP BY a.media_type"

Totals = dict[str, int]


def _evaluating(statement: str, terms: Sequence[Term]) -> tuple[str, list[Any]]:
    """`_EVALUATE` or `_EVALUATE_AFTER` naming each term `tN` by position, and the terms' values
    in `?` order. Only each term's constant condition goes in as text."""
    text = ", ".join(f"({one.condition}) AS t{n}" for n, one in enumerate(terms))
    return statement.replace("<<TERMS>>", text), [value for one in terms for value in one.params]


def _walking(term: Term) -> str:
    """`_WALK` for one term, its constant condition the only text that goes in."""
    return _WALK.replace("<<SUMS>>", f"SUM(({term.condition})) AS n")


class _Fold:
    """What evaluating some files changes: their rows, and the counts moved per bit and kind."""

    def __init__(self) -> None:
        self.rows: dict[str, tuple[str, int]] = {}
        self.dropped: list[str] = []
        self.counts: dict[tuple[int, str], int] = {}

    def add(self, bit: int, kind: str, by: int) -> None:
        self.counts[(bit, kind)] = self.counts.get((bit, kind), 0) + by

    def file(
        self, asset_id: str, stored: tuple[str, int] | None, now: Any, bits: Sequence[int]
    ) -> None:
        """One file: `stored` its kind and mask, `now` its evaluated row (None: gone)."""
        kind, mask = stored if stored is not None else (None, 0)
        if now is not None and kind != now["kind"]:
            # Every bit it carries moves with it, so each count stays the sum of its rows.
            for bit in _bits_of(mask):
                self.add(bit, str(kind), -1)
                self.add(bit, str(now["kind"]), 1)
            kind = str(now["kind"])
        if kind is None:
            return
        for n, bit in enumerate(bits):
            wanted = now is not None and bool(now[f"t{n}"])
            if wanted != bool(mask >> bit & 1):
                mask ^= 1 << bit
                self.add(bit, kind, 1 if wanted else -1)
        if stored is not None and (kind, mask) == stored:
            return
        if mask:
            self.rows[asset_id] = (kind, mask)
        elif stored is not None:
            self.dropped.append(asset_id)

    async def write(self, connection: Connection) -> None:
        if self.rows:
            await connection.executemany(
                _KEEP_FILE, [(key, kind, mask) for key, (kind, mask) in self.rows.items()]
            )
        if self.dropped:
            await connection.executemany(_DROP_FILE, [(key,) for key in self.dropped])
        moved_counts = [(bit, kind, by) for (bit, kind), by in self.counts.items() if by]
        if moved_counts:
            await connection.executemany(_ADD_COUNT, moved_counts)


def _bits_of(mask: int) -> list[int]:
    return [bit for bit in range(TERMS_KEPT) if mask >> bit & 1]


async def _apply(
    connection: Connection, ids: Sequence[str], rows: Iterable[Any], bits: Sequence[int]
) -> None:
    """Bring these files' rows and the counts in step with their evaluated `rows`."""
    stored = {
        str(row["asset_id"]): (str(row["kind"]), int(row["mask"]))
        for row in await connection.execute_fetchall(_FILES_OF, (json.dumps(list(ids)),))
    }
    now = {str(row["id"]): row for row in rows}
    fold = _Fold()
    for asset_id in ids:
        fold.file(asset_id, stored.get(asset_id), now.get(asset_id), bits)
    await fold.write(connection)


class StoredCounts:
    """The terms' counts, read from their rows. One per database."""

    def __init__(self, database: Database, clock: Any) -> None:
        self._db = database
        self._clock = clock
        #: The terms this process is building: a reader asking one meanwhile walks instead.
        self._building: set[str] = set()
        self._builds: set[asyncio.Task[None]] = set()

    async def settled(self) -> None:
        """Wait for the builds under way to end."""
        while self._builds:
            await asyncio.gather(*self._builds)

    async def totals(self, terms: Sequence[Term]) -> list[Totals] | None:
        """Each term's count per media kind, in order; None where the caller must walk: a term
        not built yet, which is then built in the background, or one a check found wrong."""
        unique = list(dict.fromkeys(terms))
        if len(unique) > TERMS_KEPT or any(one.signature in self._building for one in unique):
            return None
        bits = await self._ensure(unique)
        if bits is None:
            return None
        async with self._db.write() as connection:
            if not await self._fold(connection, unique):
                return None
            rows = await connection.execute_fetchall(
                _COUNTS_OF, (json.dumps(sorted(bits.values())),)
            )
            totals: dict[int, Totals] = {bit: {} for bit in bits.values()}
            for row in rows:
                totals[int(row["bit"])][str(row["kind"])] = int(row["n"])
            if not await self._check(connection, unique, bits, totals):
                return None
        return [totals[bits[one.signature]] for one in terms]

    async def _ensure(self, terms: Sequence[Term]) -> dict[str, int] | None:
        """Each term's bit; None once the terms not kept yet are being built."""
        now = int(self._clock())
        async with self._db.write() as connection:
            known = {
                str(row["signature"]): row
                for row in await connection.execute_fetchall(
                    _TERMS_OF, (json.dumps([one.signature for one in terms]),)
                )
            }
            # Left half built by a process that stopped: started again from nothing.
            for signature, row in list(known.items()):
                if row["building"]:
                    await _forget(connection, int(row["bit"]))
                    del known[signature]
            bits = {key: int(row["bit"]) for key, row in known.items()}
            new = [one for one in terms if one.signature not in bits]
            if new:
                bits.update(await _take_bits(connection, new, set(bits.values()), now))
                # Claimed before the writer is let go, so no other reader takes them as left over.
                self._building |= {one.signature for one in new}
        if not new:
            return bits
        # In the background: the asker walks this once rather than wait for the library.
        task = asyncio.create_task(self._build(new, bits))
        self._builds.add(task)
        task.add_done_callback(self._builds.discard)
        return None

    async def _build(self, terms: Sequence[Term], bits: dict[str, int]) -> None:
        """Count new terms over every file, a page per write so the writer is never held long."""
        signatures = {one.signature for one in terms}
        try:
            statement, params = _evaluating(_EVALUATE_AFTER, terms)
            order = [bits[one.signature] for one in terms]
            after = ""
            while True:
                async with self._db.write() as connection:
                    rows = list(
                        await connection.execute_fetchall(
                            statement, (*params, after, _BUILD_AT_ONCE)
                        )
                    )
                    ids = [str(row["id"]) for row in rows]
                    await _apply(connection, ids, rows, order)
                if len(ids) < _BUILD_AT_ONCE:
                    break
                after = ids[-1]
            async with self._db.write() as connection:
                await connection.executemany(_BUILT, [(bit,) for bit in order])
        except Exception:
            # Left marked as building, so the next asker starts it again; the count walks meanwhile.
            log.exception("backlog.build_stopped", terms=len(terms))
        finally:
            self._building -= signatures

    async def _fold(self, connection: Connection, terms: Sequence[Term]) -> bool:
        """Every file marked since each term was last read, evaluated again for it. False when a
        term was given up meanwhile to another reader's."""
        (clock,) = await connection.execute_fetchall(_NOW, ())
        top = int(clock[0])
        rows = await connection.execute_fetchall(
            _TERMS_OF, (json.dumps([one.signature for one in terms]),)
        )
        by_term = {str(row["signature"]): row for row in rows}
        if len(by_term) < len(terms):
            return False
        groups: dict[int, list[Term]] = {}
        for one in terms:
            groups.setdefault(int(by_term[one.signature]["folded_to"]), []).append(one)
        for folded_to, group in groups.items():
            if folded_to >= top:
                continue
            ids = [
                str(row["asset_id"])
                for row in await connection.execute_fetchall(_MOVED_SINCE, (folded_to,))
            ]
            statement, params = _evaluating(_EVALUATE, group)
            order = [int(by_term[one.signature]["bit"]) for one in group]
            for start in range(0, len(ids), _FOLD_AT_ONCE):
                page = ids[start : start + _FOLD_AT_ONCE]
                evaluated = await connection.execute_fetchall(
                    statement, (*params, json.dumps(page))
                )
                await _apply(connection, page, evaluated, order)
        every = json.dumps([int(row["bit"]) for row in rows])
        await connection.execute(_FOLDED, (top, int(self._clock()), every))
        await connection.execute(_PRUNE, ())
        return True

    async def _check(
        self,
        connection: Connection,
        terms: Sequence[Term],
        bits: dict[str, int],
        totals: dict[int, Totals],
    ) -> bool:
        """Walk the term checked longest ago, once it is due, and compare. A count that disagrees
        is logged and forgotten, to be built again; False then, and the caller walks."""
        now = int(self._clock())
        rows = await connection.execute_fetchall(
            _TERMS_OF, (json.dumps([one.signature for one in terms]),)
        )
        due = [row for row in rows if now - int(row["checked_at"]) >= CHECK_EVERY_SECONDS]
        if not due:
            return True
        row = min(due, key=lambda one: int(one["checked_at"]))
        bit = int(row["bit"])
        term = next(one for one in terms if one.signature == row["signature"])
        walked = await connection.execute_fetchall(_walking(term), term.params)
        found = {str(one["kind"]): int(one["n"]) for one in walked if one["n"]}
        await connection.execute(_CHECKED, (now, bit))
        if found == totals[bit]:
            return True
        log.warning("backlog.mismatch", kept=sum(totals[bit].values()), walked=sum(found.values()))
        await _forget(connection, bit)
        return False


async def _take_bits(
    connection: Connection, terms: Sequence[Term], keep: set[int], now: int
) -> dict[str, int]:
    """A free bit for each new term, the least recently asked others giving theirs up."""
    taken = [int(row["bit"]) for row in await connection.execute_fetchall(_BITS_TAKEN, ())]
    free = [bit for bit in range(TERMS_KEPT) if bit not in taken]
    for bit in taken:
        if len(free) >= len(terms):
            break
        if bit not in keep:
            await _forget(connection, bit)
            free.append(bit)
    (clock,) = await connection.execute_fetchall(_NOW, ())
    bits = dict(zip((one.signature for one in terms), free, strict=False))
    await connection.executemany(
        _NEW_TERM, [(bit, key, int(clock[0]), now, now) for key, bit in bits.items()]
    )
    return bits


async def _forget(connection: Connection, bit: int) -> None:
    """A term and its counts gone, its bit cleared from every file."""
    await connection.execute(_FORGET_TERM, (bit,))
    await connection.execute(_FORGET_COUNTS, (bit,))
    await connection.execute(_CLEAR_BIT, (bit, bit))
    await connection.execute(_DROP_EMPTY, ())
