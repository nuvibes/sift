# SPDX-License-Identifier: AGPL-3.0-or-later
"""Three tables: a User's days added up, how far the adding-up has got, and the recaps made.

## Why rows of sums, and not a query over the raw tables every time the page is drawn

The raw tables are kept for ever (`plays`, `theater_sessions`, `opinions`, the ledger), and a year
of one User's viewing is tens of thousands of sittings. A page that re-read all of it for "your
year" would be a whole-history scan on every visit, and a recap would be one again. So each
FINISHED day is added up once, for one User, into a handful of rows (a metric, a key, a number)
and a year is then 365 small reads of rows that never change. Today is the exception: it is still
happening, so it is counted live from the raw tables with the very same statements
(`store.today`), and a reader cannot tell the two apart.

**Not a second store.** Nothing here is a fact the raw tables do not hold: every row can be thrown
away and added up again from them. That is what makes it safe to change a metric's definition later:
a version step forgets how far each User's adding-up has got (`insight_progress`), and the helper
adds every day up again from the User's first, one day at a time. The day's old rows need no
separate delete: filing a day replaces all of its rows (`store.write_day`). Deleting the rows
alone would do nothing: the helper only ever moves forward from its progress. The recaps and the
achievements already made are frozen and stay as they were.

## The vault split, and why it is two numbers rather than one per vault state

Every figure is counted over everything the User may see, hidden things included, and the part of
it that came from hidden things is kept beside it. A reader shows `whole` while the vault is open
and `whole - hidden` while it is locked, so WHAT IS HIDDEN IS DECIDED WHEN THE PAGE IS DRAWN, not
when the day was added up. `split_at` is the User's `cache_stamp` when `hidden` was last worked out;
the stamp rises on every change to what that User may see, so a row whose `split_at` is behind it
has a stale split and is worked out again before it is read (`store.rows`). Only `hidden` is ever
re-worked-out: `whole` is what happened, and hiding something later does not change what happened.

## Keys

`insight_days` is WITHOUT ROWID with its primary key in reading order, because the one way it is
read is "this User, these days" and the rows are narrow: the table is its own index.

`insight_progress` and `recaps` go when their User goes, like every other row that is only about
one User. `insight_progress` names its User with a key for that reason: without it a deleted User
would leave a row behind that nothing ever reads.

## What is captured here because nothing else keeps it (version 6)

Three records Insights adds up later and that cannot be worked out afterwards, kept for ever as
`plays` are, each per User and each gone when its User goes:

- **`page_visits`**: a page about a thing or a place opened and how long it stayed in front (a hidden
  tab does not count), with the device and the sitting it was part of. One row a visit, written by
  the client in batches (`capture`), never by a read of a wall.
- **`app_sessions`**: a sitting with Sift on one device, from its first activity to its last, broken
  by a gap (`capture.SESSION_GAP_MS`). `sessions.last_seen_at` is the sign-in's, overwritten, and
  says nothing about how long anybody stayed.
- **`file_departures`**: a file that left the library, written by a trigger the moment its row goes,
  whoever removed it and however: when it arrived, its size, kind and length, and the Sites it was
  under. `file_departure_viewers` keeps who could see it then and whether it was hidden from them,
  because a departed file has no verdict left to ask. What arrived and what left on a past day is
  counted from these and from the files still here (`metrics`), so a day's figures do not shrink
  when its files are deleted and the day is added up again.
"""

from __future__ import annotations

import json
import time

from sift.kernel.db import Connection, register_schema_initializer
from sift.kernel.ids import is_id, timestamp_ms
from sift.kernel.ledger import Actor, record_event
from sift.kernel.log import get_logger
from sift.kernel.migrations import table_exists
from sift.kernel.vocabulary import DEPARTURES_KEPT, VIA_UPDATE, Subject

log = get_logger(__name__)

COMPONENT = "insights"
VERSION = 6

_CREATE_DAYS = """
CREATE TABLE IF NOT EXISTS insight_days (
  user_id  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  day      TEXT NOT NULL,
  metric   TEXT NOT NULL,
  key      TEXT NOT NULL DEFAULT '',
  whole    INTEGER NOT NULL,
  hidden   INTEGER NOT NULL DEFAULT 0,
  split_at INTEGER NOT NULL,
  PRIMARY KEY (user_id, day, metric, key)
) WITHOUT ROWID
"""

_CREATE_PROGRESS = """
CREATE TABLE IF NOT EXISTS insight_progress (
  user_id     TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  added_up_to TEXT NOT NULL
)
"""

_CREATE_RECAPS = """
CREATE TABLE IF NOT EXISTS recaps (
  id       TEXT PRIMARY KEY,
  user_id  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  period   TEXT NOT NULL,
  made_at  INTEGER NOT NULL,
  seen_at  INTEGER,
  body     TEXT NOT NULL,
  UNIQUE (user_id, period)
)
"""

#: The rows whose split is behind their User's stamp, found without walking the User's years: the
#: helper asks it every piece, and a reader asks it for the days it is about to draw.
_STALE_INDEX = "CREATE INDEX IF NOT EXISTS ix_insight_days_split ON insight_days(user_id, split_at)"


#: Version 3 keys `sittings:file` as `<kind>:<asset id>` (`metrics._SITTINGS_FILE`), so every day
#: is added up again: the helper starts over from each User's first day (see "Not a second store").
#: Version 4 does the same once more for the audited statements: a sitting's time shared between
#: the clock hours it ran through (`viewed_ms:hour`), and the files rated each day (`rated:file`).
#: Version 5 once more, for a new figure every day can hold: the time spent on each song's files
#: (`viewed_ms:song`), over the songs the files carry when the day is added up again.
#: Version 6 once more, for the arrivals and removals of a past day, which are counted from records
#: that outlive the file (`file_departures`) from then on.
_FORGET_PROGRESS = "DELETE FROM insight_progress"

# --- version 6: what nothing else keeps ---------------------------------------------------------

#: A sitting with Sift on one device. Times in milliseconds, so the order of the pages in one sitting
#: is the order they were opened in.
_CREATE_APP_SESSIONS = """
CREATE TABLE IF NOT EXISTS app_sessions (
  id            TEXT PRIMARY KEY,
  user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  device_id     TEXT,
  client_kind   TEXT NOT NULL,
  started_at_ms INTEGER NOT NULL,
  last_at_ms    INTEGER NOT NULL
)
"""

#: One page opened. `place` is what kind of page (a person's, a wall, Settings), `ref` which one
#: (the thing's id, the wall's name, the Settings section, the Organize queue; empty where the
#: page is the only one of its kind). `hidden` is whether the thing was in Hidden when the visit
#: was written, which is only ever true for somebody who had unlocked it: nothing is written for a
#: page the vault keeps from the person asking. `id` is the client's own, so a visit reported again
#: as it goes on (`capture.record_visits`) is one row brought up to date, never a second.
_CREATE_PAGE_VISITS = """
CREATE TABLE IF NOT EXISTS page_visits (
  id             TEXT PRIMARY KEY,
  user_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  app_session_id TEXT NOT NULL REFERENCES app_sessions(id) ON DELETE CASCADE,
  place          TEXT NOT NULL,
  ref            TEXT NOT NULL DEFAULT '',
  hidden         INTEGER NOT NULL DEFAULT 0,
  opened_at_ms   INTEGER NOT NULL,
  last_at_ms     INTEGER NOT NULL,
  front_ms       INTEGER NOT NULL,
  device_id      TEXT,
  client_kind    TEXT NOT NULL
)
"""

#: A file that left the library. No key to `assets`, on purpose: the row exists because the file
#: does not. `arrived_at` and `ended_at` in seconds, as `assets.added_at` is. `site_ids` is a JSON
#: list of the Sites the file was under, for the arrivals by Site. `viewers_known` is 0 on a row
#: brought back from History by the version 6 step, which cannot say who could see the file.
_CREATE_FILE_DEPARTURES = """
CREATE TABLE IF NOT EXISTS file_departures (
  asset_id      TEXT PRIMARY KEY,
  arrived_at    INTEGER,
  ended_at      INTEGER NOT NULL,
  size_bytes    INTEGER,
  media_type    TEXT,
  duration_ms   INTEGER,
  site_ids      TEXT NOT NULL DEFAULT '[]',
  viewers_known INTEGER NOT NULL DEFAULT 1
)
"""

_CREATE_FILE_DEPARTURE_VIEWERS = """
CREATE TABLE IF NOT EXISTS file_departure_viewers (
  user_id   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  asset_id  TEXT NOT NULL REFERENCES file_departures(asset_id) ON DELETE CASCADE,
  concealed INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, asset_id)
) WITHOUT ROWID
"""

_CAPTURE_INDEXES = (
    # A User's visits in time order: a day's, a year's.
    "CREATE INDEX IF NOT EXISTS ix_page_visits_user ON page_visits(user_id, opened_at_ms)",
    # The pages of one sitting in order, and the cascade from a sitting to its visits.
    "CREATE INDEX IF NOT EXISTS ix_page_visits_sitting"
    " ON page_visits(app_session_id, opened_at_ms)",
    # The sitting a new visit on this device joins: the latest one, by its last activity.
    "CREATE INDEX IF NOT EXISTS ix_app_sessions_device"
    " ON app_sessions(user_id, device_id, last_at_ms)",
    # A day's arrivals and a day's removals, never the whole table.
    "CREATE INDEX IF NOT EXISTS ix_file_departures_arrived ON file_departures(arrived_at)",
    "CREATE INDEX IF NOT EXISTS ix_file_departures_ended ON file_departures(ended_at)",
)

#: WRITTEN AS THE ROW GOES, whoever removes it: the delete feature, the leftovers sweep, anything
#: later. BEFORE the delete, because the rows that say who could see the file and which Sites it
#: was under cascade away with it. Guarded by NOT EXISTS rather than OR IGNORE, because a delete
#: reached by a cascade runs its triggers under the cascade's ABORT and an OR IGNORE would be
#: overridden. The viewers are read one User at a time by the verdict's own key, so a file going
#: costs a probe per User and never a walk of the verdict.
_FILE_DEPARTS = """
CREATE TRIGGER IF NOT EXISTS insights_file_departs BEFORE DELETE ON assets
BEGIN
  INSERT INTO file_departures
    (asset_id, arrived_at, ended_at, size_bytes, media_type, duration_ms, site_ids, viewers_known)
  SELECT OLD.id, OLD.added_at, CAST(strftime('%s', 'now') AS INTEGER), OLD.size_bytes,
         OLD.media_type, OLD.duration_ms,
         (SELECT json_group_array(DISTINCT u.site_id)
            FROM asset_usernames au JOIN usernames u ON u.id = au.username_id
           WHERE au.asset_id = OLD.id AND u.site_id IS NOT NULL),
         1
   WHERE NOT EXISTS (SELECT 1 FROM file_departures f WHERE f.asset_id = OLD.id);
  INSERT INTO file_departure_viewers (user_id, asset_id, concealed)
  SELECT va.user_id, OLD.id, va.concealed
    FROM users x JOIN viewer_assets va ON va.user_id = x.id AND va.asset_id = OLD.id
   WHERE NOT EXISTS (SELECT 1 FROM file_departure_viewers d
                      WHERE d.user_id = va.user_id AND d.asset_id = OLD.id);
END
"""

#: Every file History says was deleted before the trigger above existed, and that is not here now:
#: its id and when it went. What else it was (size, kind, length, Sites) went with it unrecorded.
#:
#: A one-time schema step, run as the system with no viewer to scope to, and its one mention of
#: `assets` asks only that a file is NOT there: nothing of any file's row comes back.
_DELETED_BEFORE = (
    "SELECT s.subject_id AS asset_id, MIN(d.decided_at) AS ended_at, MAX(s.name) AS name"
    " FROM workbench_decisions d"
    " JOIN workbench_decision_subjects s ON s.decision_id = d.id AND s.kind = 'asset'"
    " WHERE d.verb = 'deleted'"
    " AND NOT EXISTS (SELECT 1 FROM assets a WHERE a.id = s.subject_id)"  # nosemgrep: sift-no-asset-sql-outside-kernel
    " AND NOT EXISTS (SELECT 1 FROM file_departures f WHERE f.asset_id = s.subject_id)"
    " GROUP BY s.subject_id"
    " ORDER BY MIN(d.decided_at), s.subject_id"
)

_KEEP_DELETED_BEFORE = """
INSERT INTO file_departures (asset_id, arrived_at, ended_at, viewers_known)
VALUES (?, ?, ?, 0)
"""


async def keep_what_is_still_known(connection: Connection) -> int:
    """Bring back what History can still say about the files deleted before departures were kept.

    The one-time half of `file_departures`: a row for every file a "deleted" line names that is
    neither here nor already kept, with when it went (the line's moment) and when it arrived, read
    off its id, which is minted the moment a file is first indexed and carries that moment in it.
    Its size, kind and length cannot be known any more and stay empty; who could see it is not
    known either (`viewers_known` 0), so an admin's figures count it and nobody else's do.

    It SAYS WHAT IT DID with one History line for the library, in this same write: "Sift kept a
    record of the 13 files deleted before this update", about the first of them by the name its
    deleted line gave it, with the count. No Undo: nothing is taken off anything. No line when it
    kept nothing.

    Safe to run twice: a file kept once is never kept again. Answers how many it kept.
    """
    if not await table_exists(connection, "workbench_decisions"):
        return 0
    rows = list(await connection.execute_fetchall(_DELETED_BEFORE))
    for row in rows:
        asset_id = str(row["asset_id"])
        arrived = timestamp_ms(asset_id) // 1000 if is_id(asset_id) else None
        await connection.execute(_KEEP_DELETED_BEFORE, (asset_id, arrived, int(row["ended_at"])))
    if rows:
        first = rows[0]
        await record_event(
            connection,
            actor=Actor.sift(VIA_UPDATE),
            verb="added",
            subject=Subject(
                kind="asset", id=str(first["asset_id"]), name=str(first["name"] or "a file")
            ),
            count=len(rows),
            payload=json.dumps({DEPARTURES_KEPT: len(rows)}),
        )
    return len(rows)


async def initialize(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        await connection.execute(_CREATE_DAYS)
        await connection.execute(_STALE_INDEX)
        await connection.execute(_CREATE_PROGRESS)
        await connection.execute(_CREATE_RECAPS)
    if on_disk < 6:
        for statement in (
            _CREATE_APP_SESSIONS,
            _CREATE_PAGE_VISITS,
            _CREATE_FILE_DEPARTURES,
            _CREATE_FILE_DEPARTURE_VIEWERS,
            *_CAPTURE_INDEXES,
            _FILE_DEPARTS,
        ):
            await connection.execute(statement)
        started = time.monotonic()
        kept = await keep_what_is_still_known(connection)
        # The count and nothing else: what the files were called is History's to say.
        log.info(
            "insights.departures_kept",
            kept=kept,
            seconds=round(time.monotonic() - started, 3),
        )
        await connection.execute(_FORGET_PROGRESS)


# `users` comes from the identity component, so it is built before these keys name it. The trigger
# on `assets` reads the verdict, the usernames and the Sites, so the components that make those come
# first; the History the version 6 step reads is asked for rather than required (a database built
# for one feature's tests has none).
register_schema_initializer(
    COMPONENT,
    VERSION,
    initialize,
    depends_on=["identity", "content", "catalog", "visibility"],
    baseline=2,
)
