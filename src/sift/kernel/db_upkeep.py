# SPDX-License-Identifier: AGPL-3.0-or-later
"""The write-ahead log kept small: copied back beside the writer, folded under its lock."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from sift.kernel.db_base import DatabaseError
from sift.kernel.db_hooks import (
    _COPY_UNDER_GUARD_PAGES,
    _IN_WRITE,
    _LOG_CAP_PAGES,
    _MOST_LOG_COPIES,
    _copy_back,
)
from sift.kernel.db_judged import _MAINTENANCE
from sift.kernel.log import get_logger

if TYPE_CHECKING:
    import asyncio

    import aiosqlite

log = get_logger(__name__)
#: Said by anything that takes the writer when this task already holds it.
_INSIDE_A_WRITE = (
    "write() cannot be opened inside another write(): it would wait for a lock this task already "
    "holds and never return. Pass the connection you already have down to whatever needs it, so "
    "the whole write is one transaction."
)


class LogUpkeep:
    """Mixed into `Database`: the log's own statements, which are nobody's write."""

    _folder_lock: asyncio.Lock
    _folder: aiosqlite.Connection | None
    _write_lock: asyncio.Lock

    async def _open(self, *, writer: bool = False) -> aiosqlite.Connection:
        raise NotImplementedError

    def _require_writer(self) -> aiosqlite.Connection:
        raise NotImplementedError

    async def _maintained[T](self, work: Awaitable[T]) -> T:
        """Run one of the database's own statements marked as such for the record."""
        token = _MAINTENANCE.set(True)
        try:
            return await work
        finally:
            _MAINTENANCE.reset(token)

    @asynccontextmanager
    async def _held_for_upkeep(self) -> AsyncIterator[aiosqlite.Connection]:
        """The writer's lock for one of the database's own statements, in no transaction: not a
        write block, and refused inside one, where it would wait on a lock this task holds."""
        if _IN_WRITE.get():
            raise DatabaseError(_INSIDE_A_WRITE)
        async with self._write_lock:
            yield self._require_writer()

    async def copy_the_log_back(self) -> tuple[int, int]:
        """Copy the write-ahead log into the database: (pages in the log, pages copied).

        Most of it on a connection beside the writer; what the writer added meanwhile under its
        lock, a short step, so the next write starts the log over rather than grow it."""
        async with self._folder_lock:
            if self._folder is None:
                self._folder = await self._open()
            pages, copied = await self._maintained(_copy_back(self._folder))
            # Again while the writer added much during the last copy, so the guarded step is short;
            # skipped for a later look if it would not be, unless the log has grown past its cap.
            added = pages
            for _ in range(_MOST_LOG_COPIES):
                more, copied = await self._maintained(_copy_back(self._folder))
                added, pages = more - pages, more
                if added <= _COPY_UNDER_GUARD_PAGES:
                    break
            if added > _COPY_UNDER_GUARD_PAGES and pages < _LOG_CAP_PAGES:
                log.debug("db.log_copied", pages=pages, copied=copied, guarded=False)
                return pages, copied
            # Under the writer's lock and in no transaction: a checkpoint is not a write of the
            # application's, so it is neither a write block nor a statement the ledger hears.
            async with self._held_for_upkeep() as writer:
                began = time.monotonic()
                pages, copied = await self._maintained(_copy_back(writer))
                held_ms = round((time.monotonic() - began) * 1000, 1)
            log.debug(
                "db.log_copied",
                pages=pages,
                copied=copied,
                guarded=True,
                added=added,
                held_ms=held_ms,
            )
            return pages, copied

    async def fold_the_log_back(self) -> tuple[bool, int]:
        """Fold the write-ahead log back into the database: (did it, pages still there).

        `TRUNCATE` gives up rather than waits, so it is safe on a timer; the pages are copied first
        without the lock, so the reset under it has little left to do."""
        await self.copy_the_log_back()
        async with self._held_for_upkeep() as writer:
            token = _MAINTENANCE.set(True)
            try:
                cursor = await writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                try:
                    row = await cursor.fetchone()
                finally:
                    await cursor.close()
            finally:
                _MAINTENANCE.reset(token)
        if row is None:  # pragma: no cover (the pragma always answers)
            return False, 0
        # (busy, log pages, pages copied): on success TRUNCATE reports zero for both counts, so all
        # this can say is whether it got in.
        busy, remaining = int(row[0]), int(row[1])
        return busy == 0, remaining
