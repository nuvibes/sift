# SPDX-License-Identifier: AGPL-3.0-or-later
"""Writing down the pages a User opened, and the sittings they were part of.

## What a visit is

A page about a thing or a place, opened: a person's, a tag's, a Site's, a Collection's, a Photo
Set's, a song's, a folder, one of the walls, a section of Settings, Organize, Insights. Not a file:
a file opened is a sitting, which the player keeps. The client keeps the visit while the page is in
front, adds up the time it was in front (a hidden tab does not count) and hands visits over in
batches (`record_visits`): at most one small write per visit, never on the read of a wall. A visit
that goes on is reported again under the same id and brought up to date, so a tab left open all
evening is one row, not one a minute.

The client says how long ago a visit opened and was last in front, never a time of day: its clock
is not this computer's, and a phone set five minutes fast would otherwise put its pages in the
wrong order beside the sittings this computer stamps.

## A sitting with Sift (`app_sessions`)

From the first visit on a device to the last, and broken by `SESSION_GAP_MS` with nothing in front.
Thirty minutes: long enough that answering the door, a phone call or making a drink between two
pages is the same sitting, short enough that coming back after lunch is a new one; it is also the
gap most measures of "a visit to a site" use, so a figure here reads the way people expect. Each
device has its own: a phone in one hand and the desk in front of somebody are two sittings, which
is what "where do I use Sift" wants to count.

## What is never written

A page about something the person asking may not be shown: the access layer is asked, as every
read is, and a page it refuses leaves no row. A page about something in Hidden is written only for
somebody who has unlocked it, with `hidden` set so a reader can leave it out for them once it is
locked again. And nothing at all while the User has paused their history (`kernel/use_history.py`).

## How many rows

One per page opened. A busy day opens a few hundred pages, which is a few hundred narrow rows a
day, under a megabyte a year: kept for ever, as sittings are, because a year's "time
browsing" and "your longest sitting" are the questions they are kept for.
"""

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
    """Whether the page's thing is in Hidden for this person (True), shown to them plainly (False),
    or not theirs to be shown at all (None). A place, not a thing, is shown plainly."""
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
    """Write down these visits for this person, from this client. Answers how many were written.

    Nothing while their history is paused, nothing for a page they may not be shown. The visits
    are taken oldest first, so the pages of one sitting join it in the order they were opened.
    """
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

# The visits first and then their sittings, so the count says how many of each went (a sitting
# would take its visits with it, uncounted). The figures added up from the history are
# added up again from what is left: the progress goes, and the helper starts again from the first
# day (see `schema`). The recaps go too: each is a summary of what is being cleared.
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
