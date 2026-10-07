# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the one writer says while it is held, and when the write-ahead log is offered a fold."""

from __future__ import annotations

import asyncio
import time
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


def _writer_still_held(connection: _JudgedWriter, began: float, every: float) -> None:
    """Said once, then again every `every` seconds until the block's end cancels it."""
    log.warning(
        "db.writer_held",
        seconds=round(time.monotonic() - began, 1),
        statements=connection.statements,
        statement=connection.last_statement,
    )
    asyncio.get_running_loop().call_later(every, _writer_still_held, connection, began, every)


async def keep_the_log_folded(
    database: Database,
    stop: asyncio.Event,
    *,
    interval: float = CHECKPOINT_INTERVAL_SECONDS,
) -> None:
    """Offer the write-ahead log a chance to fold back, on a timer, until told to stop. Offered,
    never forced: a busy install skips one and takes the next, and without this the moment the log
    needs never arrives under continuous background work."""
    while not stop.is_set():
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
        if stop.is_set():
            return
        try:
            # Sized either side, because the pragma cannot say how much it reclaimed.
            before = await asyncio.to_thread(_log_bytes, database.path)
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
