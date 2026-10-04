# SPDX-License-Identifier: AGPL-3.0-or-later
"""One row for every time somebody sat down in front of a Theater wall.

## Why a row of its own, and not a sum of the sittings

Every cell already writes a sitting for each file it shows (`plays`, with `screen = 'theater'` and
this session's name on it). What those rows cannot say is the WALL: nine cells left running for an
hour are nine hours of sittings and one hour of somebody's evening, and nothing in nine rows says
they were one wall, how it was laid out, what each cell was drawing from, or which saved wall it
was. None of that can be worked out afterwards, so it is written down as it happens.

## Written the way a sitting is: facts in, reported from the browser, twice

The wall reports when it opens and again when it closes. Both are the same report and both are the
same one statement: an upsert keyed on the name the wall minted, so the second report finds the
first and finishes it, and a lost first report costs nothing because the second writes the whole
row. `started_at` is DERIVED on the first row written, exactly as `plays.started_at` is: the
moment the report landed less how long the wall says it has been open, and never moved after.

A session whose close never arrives (a crash, a browser killed from outside) keeps a NULL
`ended_at`. That is the honest answer, and not a hole: its cells' sittings carry its name, so the
last of them says when it was last in use.

## Not the ledger's business, for the reason `plays` is not

Watching decides nothing and changes nothing in the library. This is a measurement, read only by
the user it belongs to, and it goes when that user goes.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence

from sift.kernel.db import Database
from sift.kernel.ids import new_id

_MS_PER_SECOND = 1000

#: The one statement. `started_at` only from the row's first write; everything the close knows
#: better than the open replaces it, and a value the report did not carry leaves the stored one.
#: `ended_at` is never un-set by a report arriving late, which is the only way an open could follow
#: a close. `files` never goes DOWN, for the same reason.
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
    """Write down one report of a Theater session: its opening, or its end.

    Facts only, as the client saw them. `sources` is each drawn cell's query, in the order the
    wall draws them: the same text a saved wall keeps for a cell, so a reader can tell a Loop
    from a filter by reading it exactly as the wall does.
    """
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
