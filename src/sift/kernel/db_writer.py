# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the one writer says while it is held, and when the write-ahead log is offered a fold."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from sift.kernel.db_judged import _JudgedWriter
from sift.kernel.log import get_logger

if TYPE_CHECKING:
    from sift.kernel.db import Database

log = get_logger(__name__)

#: How often the write-ahead log is offered a fold: rarely, since a landed one is a burst of copying.
CHECKPOINT_INTERVAL_SECONDS = 300.0

#: How often the keeper looks at the log's size, and the size at which it copies the log back
#: without the write lock: about the 1,000 pages SQLite's own copy waits for, which commits do not
#: run here (`Database._open`).
LOG_LOOK_SECONDS = 0.25
COPY_BACK_AT_BYTES = 4 * 1024 * 1024


#: Below this much reclaimed, folding the log is not worth a line in anybody's log.
RECLAIM_WORTH_SAYING_BYTES = 8 * 1024 * 1024


def _log_bytes(database_path: Path) -> int:
    """How big the write-ahead log is right now. Zero when there is not one."""
    try:
        return (database_path.parent / (database_path.name + "-wal")).stat().st_size
    except OSError:
        return 0


#: A write block over this is said when it ends: it is the budget of a press's own writes, which
#: queue behind it. A block that is still held after `WRITER_HELD_SECONDS` is said every so many
#: seconds while it runs, with the statement it is on.
WRITE_HELD_BUDGET_MS = 250
WRITER_HELD_SECONDS = 5.0
#: Under load many writes run over budget; one line this often carries the count of the rest.
WRITE_HELD_SAY_SECONDS = 5.0


class _WaitsSaid:
    """Waits for the writer over the budget, said as one line per `WRITE_HELD_SAY_SECONDS` that
    carries the count and the worst of the rest."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._said = 0.0
        self._quietly = 0
        self._worst_ms = 0.0

    def waited(self, waited_ms: float) -> None:
        if waited_ms < WRITE_HELD_BUDGET_MS:
            return
        now = self._clock()
        self._quietly += 1
        self._worst_ms = max(self._worst_ms, waited_ms)
        if now - self._said < WRITE_HELD_SAY_SECONDS:
            return
        log.info(
            "db.write_waited",
            waited_ms=round(waited_ms),
            worst_ms=round(self._worst_ms),
            blocks=self._quietly,
        )
        self._said, self._quietly, self._worst_ms = now, 0, 0.0


def _writer_still_held(
    connection: _JudgedWriter, began: float, every: float, next_one: list[asyncio.TimerHandle]
) -> None:
    """Said once, then again every `every` seconds until the block's end cancels `next_one`, which
    always holds the next: a line cancelled at its first would go on for ever, every few seconds."""
    log.warning(
        "db.writer_held",
        seconds=round(time.monotonic() - began, 1),
        statements=connection.statements,
        statement=connection.last_statement,
    )
    next_one[:] = [
        asyncio.get_running_loop().call_later(
            every, _writer_still_held, connection, began, every, next_one
        )
    ]


async def keep_the_log_folded(
    database: Database,
    stop: asyncio.Event,
    *,
    interval: float = CHECKPOINT_INTERVAL_SECONDS,
    look: float = LOG_LOOK_SECONDS,
    copy_at: int = COPY_BACK_AT_BYTES,
) -> None:
    """Keep the write-ahead log small until told to stop: copied back without the write lock once
    it reaches `copy_at`, and offered a fold on a timer. Offered, never forced: a fold is skipped
    while the writer is held and taken at a later tick, and without this the moment the log needs
    never arrives under continuous background work."""
    step = min(look, interval)
    # Counted in looks rather than read off a clock, so a fold falls on a look whatever the timer's
    # resolution.
    looks_per_fold = max(1, round(interval / step))
    looks = 0
    while not stop.is_set():
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=step)
        if stop.is_set():
            return
        looks += 1
        try:
            # Sized either side, because the pragma cannot say how much it reclaimed.
            before = await asyncio.to_thread(_log_bytes, database.path)
            if looks < looks_per_fold or database.writer_is_held():
                if before >= copy_at:
                    await database.copy_the_log_back()
                continue
            looks = 0
            folded, remaining = await database.fold_the_log_back()
            after = await asyncio.to_thread(_log_bytes, database.path)
            if folded and before - after >= RECLAIM_WORTH_SAYING_BYTES:
                log.info(
                    "db.log_folded",
                    reclaimed_mb=round((before - after) / 1_000_000, 1),
                    log_mb=round(after / 1_000_000, 1),
                )
            elif not folded:
                # Not a failure; a quiet line so a log that never shrinks has an explanation.
                log.debug("db.log_busy", log_mb=round(after / 1_000_000, 1), pages=remaining)
        except Exception:
            # Housekeeping does not get to take the application down with it.
            log.exception("db.log_fold_failed")
