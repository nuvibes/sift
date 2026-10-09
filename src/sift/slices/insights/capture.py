# SPDX-License-Identifier: AGPL-3.0-or-later
"""Writing down the pages a User opened, and the sittings they were part of."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from typing import Final

from sift.kernel.access import Concealment, Repository, Viewer
from sift.kernel.client import Client
from sift.kernel.db import Connection, Database
from sift.kernel.ids import new_id
from sift.kernel.seams import SettingsSeam
from sift.kernel.use_history import keeps_history, register_clearing

#: The quiet that ends a sitting with Sift. See the module docstring.
SESSION_GAP_MS: Final = 30 * 60 * 1000

#: The most visits one batch may carry. A client hands them over every minute or so and on leaving
#: a page; a batch this size is an hour of somebody opening a page every fifteen seconds.
MOST_PER_BATCH: Final = 240

#: The longest a visit can say it went on, in front or open: a day. A number past it is a clock
#: that jumped or a value somebody typed, and is cut to it rather than stored.
LONGEST_MS: Final = 24 * 60 * 60 * 1000

#: What a visit's id looks like: what the client mints, nothing a row could be addressed by.
VISIT_ID: Final = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

#: The pages that are about one thing, by the place word, and how the access layer is asked
#: whether this person may be shown it.
_Asks = Callable[[Repository, Viewer, str], Awaitable[object | None]]
THINGS: Final[dict[str, _Asks]] = {
    "person": lambda access, viewer, ref: access.visible_person(viewer, ref),
    "tag": lambda access, viewer, ref: access.visible_tag(viewer, ref),
    "site": lambda access, viewer, ref: access.visible_site(viewer, ref),
    "collection": lambda access, viewer, ref: access.visible_collection(viewer, ref),
    "photo_set": lambda access, viewer, ref: access.visible_photo_set(viewer, ref),
    "song": lambda access, viewer, ref: access.visible_song(viewer, ref),
    "folder": lambda access, viewer, ref: access.get_folder(viewer, ref),
}

#: The pages that are about a place rather than a thing: `ref` names which one, as the client's
#: own word (the wall, the Settings section, the Organize queue), and nothing is asked of it.
PLACES: Final = frozenset({"wall", "settings", "organize", "insights"})


@dataclass(frozen=True, slots=True)
class Visit:
    """One visit as the client reports it. Times are how long AGO, in milliseconds."""

    id: str
    place: str
    ref: str
    opened_ago_ms: int
    last_ago_ms: int
    front_ms: int


_EXISTING = "SELECT user_id, app_session_id FROM page_visits WHERE id = ?"

_LATEST_SITTING = """
SELECT id, last_at_ms FROM app_sessions
 WHERE user_id = ? AND device_id IS ?
 ORDER BY last_at_ms DESC
 LIMIT 1
"""

_START_SITTING = """
INSERT INTO app_sessions (id, user_id, device_id, client_kind, started_at_ms, last_at_ms)
VALUES (?, ?, ?, ?, ?, ?)
"""

_GO_ON_SITTING = "UPDATE app_sessions SET last_at_ms = MAX(last_at_ms, ?) WHERE id = ?"

# The same visit reported again moves its end and its time in front forward, never back: two
# reports of one visit crossing on the wire leave the later of each.
_KEEP_VISIT = """
INSERT INTO page_visits
  (id, user_id, app_session_id, place, ref, hidden, opened_at_ms, last_at_ms, front_ms,
   device_id, client_kind)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
  last_at_ms = MAX(page_visits.last_at_ms, excluded.last_at_ms),
  front_ms = MAX(page_visits.front_ms, excluded.front_ms)
"""


def _within(ms: int) -> int:
    return max(0, min(ms, LONGEST_MS))


async def _shown(access: Repository, viewer: Viewer, visit: Visit) -> bool | None:
    """True if the page's thing is in Hidden for this person, False if plain, None if not theirs."""
    ask = THINGS.get(visit.place)
    if ask is None:
        return False
    if await ask(access, viewer, visit.ref) is None:
        return None
    if not viewer.show_hidden:
        return False
    # Asked again with Hidden shut and nothing of it drawn: a thing that is then gone was shown
    # only because this person had unlocked it.
    shut = replace(viewer, show_hidden=False, concealment=Concealment.FULLY_GONE)
    return await ask(access, shut, visit.ref) is None


async def _sitting(
    connection: Connection, viewer: Viewer, client: Client, opened: int, last: int
) -> str:
    """The sitting a new visit belongs to on this device: the latest, carried on, or a new one."""
    rows = list(await connection.execute_fetchall(_LATEST_SITTING, (viewer.id, client.device)))
    if rows and opened - int(rows[0]["last_at_ms"]) <= SESSION_GAP_MS:
        sitting = str(rows[0]["id"])
        await connection.execute(_GO_ON_SITTING, (last, sitting))
        return sitting
    sitting = new_id()
    await connection.execute(
        _START_SITTING, (sitting, viewer.id, client.device, client.kind, opened, last)
    )
    return sitting


async def record_visits(
    database: Database,
    access: Repository,
    preferences: SettingsSeam,
    viewer: Viewer,
    client: Client,
    visits: Sequence[Visit],
    *,
    now_ms: int,
) -> int:
    """Write down these visits for this person, from this client. Answers how many were written."""
    if not visits or not await keeps_history(preferences, viewer.id):
        return 0
    kept: list[tuple[Visit, bool]] = []
    for visit in visits:
        hidden = await _shown(access, viewer, visit)
        if hidden is not None:
            kept.append((visit, hidden))
    kept.sort(key=lambda pair: -pair[0].opened_ago_ms)
    written = 0
    async with database.write() as connection:
        for visit, hidden in kept:
            opened = now_ms - _within(visit.opened_ago_ms)
            last = max(opened, now_ms - _within(visit.last_ago_ms))
            front = min(_within(visit.front_ms), last - opened)
            found = list(await connection.execute_fetchall(_EXISTING, (visit.id,)))
            if found and found[0]["user_id"] != viewer.id:
                # Somebody else's id: never written over, and nothing said about it.
                continue
            if found:
                sitting = str(found[0]["app_session_id"])
                await connection.execute(_GO_ON_SITTING, (last, sitting))
            else:
                sitting = await _sitting(connection, viewer, client, opened, last)
            await connection.execute(
                _KEEP_VISIT,
                (
                    visit.id,
                    viewer.id,
                    sitting,
                    visit.place,
                    visit.ref,
                    int(hidden),
                    opened,
                    last,
                    front,
                    client.device,
                    client.kind,
                ),
            )
            written += 1
    return written


# --- clearing ----------------------------------------------------------------------------------

# Visits before sittings, so each is counted; a sitting would take its visits with it.
_CLEAR = (
    "DELETE FROM page_visits WHERE user_id = ?",
    "DELETE FROM app_sessions WHERE user_id = ?",
    "DELETE FROM insight_days WHERE user_id = ?",
    "DELETE FROM insight_progress WHERE user_id = ?",
    "DELETE FROM recaps WHERE user_id = ?",
)


async def clear_mine(connection: Connection, user_id: str) -> int:
    """Insights' part of clearing a User's history (`kernel/use_history.py`)."""
    gone = 0
    for statement in _CLEAR:
        cursor = await connection.execute(statement, (user_id,))
        gone += int(cursor.rowcount)
    return gone


register_clearing("insights", clear_mine)
