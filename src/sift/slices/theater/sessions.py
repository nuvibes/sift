# SPDX-License-Identifier: AGPL-3.0-or-later
"""One row per Theater session, the wall itself, which its cells' sittings cannot say.

Reported on open and close by one upsert; a session whose close never arrives keeps a NULL end.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence

from sift.kernel.db import Database
from sift.kernel.ids import new_id

_MS_PER_SECOND = 1000

#: `started_at` only from the first write; `ended_at` never unset and `files` never lowered.
_REPORT = (
    "INSERT INTO theater_sessions"
    " (id, user_id, session, started_at, ended_at, layout, cells, arrangement_id, sources, files,"
    " made_at)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    " ON CONFLICT(user_id, session) DO UPDATE SET"
    " ended_at = COALESCE(excluded.ended_at, theater_sessions.ended_at),"
    " layout = COALESCE(excluded.layout, theater_sessions.layout),"
    " cells = COALESCE(excluded.cells, theater_sessions.cells),"
    " arrangement_id = COALESCE(excluded.arrangement_id, theater_sessions.arrangement_id),"
    " sources = COALESCE(excluded.sources, theater_sessions.sources),"
    " files = MAX(theater_sessions.files, excluded.files),"
    " made_at = excluded.made_at"
)


async def record_session(
    database: Database,
    *,
    user_id: str,
    session: str,
    elapsed_ms: int,
    ended: bool,
    layout: str | None,
    cells: int | None,
    arrangement_id: str | None,
    sources: Sequence[str] | None,
    files: int,
) -> None:
    """Write down one report of a Theater session: its opening, or its end."""
    made_at = int(time.time())
    async with database.write() as connection:
        await connection.execute(
            _REPORT,
            (
                new_id(),
                user_id,
                session,
                made_at - max(0, elapsed_ms) // _MS_PER_SECOND,
                made_at if ended else None,
                layout,
                cells,
                arrangement_id,
                None if sources is None else json.dumps(list(sources)),
                max(0, files),
                made_at,
            ),
        )
