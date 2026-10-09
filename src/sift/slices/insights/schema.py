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
from sift.kernel.migrations import column_exists, table_exists
from sift.kernel.vocabulary import DEPARTURES_KEPT, VIA_UPDATE, Subject

log = get_logger(__name__)

COMPONENT = "insights"
VERSION = 8

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
  added_up_to TEXT NOT NULL,
  dirty_from  TEXT,
  dirty_mark  INTEGER NOT NULL DEFAULT 0
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
  metrics_version INTEGER NOT NULL DEFAULT 0,
  left_out TEXT NOT NULL DEFAULT '[]',
  UNIQUE (user_id, period)
)
"""

#: The rows whose split is behind their User's stamp, found without walking the User's years: the
#: helper asks it every piece, and a reader asks it for the days it is about to draw.
_STALE_INDEX = "CREATE INDEX IF NOT EXISTS ix_insight_days_split ON insight_days(user_id, split_at)"


#: Version 3 keys `sittings:file` as `<kind>:<asset id>` (`metrics_things.SITTINGS_FILE`), so every day
#: is added up again: the helper starts over from each User's first day (see "Not a second store").
#: Version 4 does the same once more for the audited statements: a sitting's time shared between
#: the clock hours it ran through (`viewed_ms:hour`), and the files rated each day (`rated:file`).
#: Version 5 once more, for a new figure every day can hold: the time spent on each song's files
#: (`viewed_ms:song`), over the songs the files carry when the day is added up again.
#: Version 6 once more, for the arrivals and removals of a past day, which are counted from records
#: that outlive the file (`file_departures`) from then on.
#: Version 7 once more, for the corrected statements (`metrics.METRICS_VERSION`).
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
#:
#: The verdict's own trigger on the same delete takes the file's verdict rows away, and SQLite runs
#: the triggers of one event newest first, so this one reads them only while it is the newer. Every
#: User has a verdict row for a file while it is there (an admin's says it is shown), so finding
#: none means they went first: `viewers_known` is then 0, and the file counts as an admin's
#: removal rather than as nobody's. The version 7 step makes this trigger the newer again.
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
         EXISTS (SELECT 1 FROM users x JOIN viewer_assets va
                                         ON va.user_id = x.id AND va.asset_id = OLD.id)
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


# --- version 7: a day added up again when what it counted changes -------------------------------
#
# A day is added up once, but some of what it counted is written later: a sitting reported after
# the cut, a wall closed, a run finished, a file filed under a Username, a person, a Tag, a
# Collection, a Photo Set or a song. Each such write moves the User's `dirty_from` back to the first
# day it reaches, and the helper adds those days up again before it moves on (`rollup`).
# `dirty_mark` counts the marks, so a mark landing while a day is being re-added is not lost.

_DROP_FILE_DEPARTS = "DROP TRIGGER IF EXISTS insights_file_departs"

#: Departures kept while the verdict's trigger ran first: nobody was recorded, so nobody is known.
_UNKNOWN_VIEWERS = """
UPDATE file_departures SET viewers_known = 0
 WHERE viewers_known = 1
   AND NOT EXISTS (SELECT 1 FROM file_departure_viewers v
                    WHERE v.asset_id = file_departures.asset_id)
"""

_ADD_V7 = (
    ("insight_progress", "dirty_from", "ALTER TABLE insight_progress ADD COLUMN dirty_from TEXT"),
    (
        "insight_progress",
        "dirty_mark",
        "ALTER TABLE insight_progress ADD COLUMN dirty_mark INTEGER NOT NULL DEFAULT 0",
    ),
    (
        "recaps",
        "metrics_version",
        "ALTER TABLE recaps ADD COLUMN metrics_version INTEGER NOT NULL DEFAULT 0",
    ),
)

# --- version 8: what a reader took out of a recap before sharing it ------------------------------

#: The ids of the people and files the reader left out, as a JSON list; none for every recap so far.
_ADD_LEFT_OUT = "ALTER TABLE recaps ADD COLUMN left_out TEXT NOT NULL DEFAULT '[]'"

#: The recaps made before this step were made by the statements it replaces.
_RECAPS_MADE_BEFORE = "UPDATE recaps SET metrics_version = 6 WHERE metrics_version = 0"

#: A User's own sitting or wall, on the day it began.
_REDO_PLAYS_IN = """
CREATE TRIGGER IF NOT EXISTS insights_redo_plays_in AFTER INSERT ON plays
BEGIN
  UPDATE insight_progress
     SET dirty_from = MIN(COALESCE(dirty_from, added_up_to),
                          date(NEW.started_at, 'unixepoch', 'localtime')),
         dirty_mark = dirty_mark + 1
   WHERE user_id = NEW.user_id
     AND added_up_to >= date(NEW.started_at, 'unixepoch', 'localtime');
END
"""

_REDO_PLAYS_MOVED = """
CREATE TRIGGER IF NOT EXISTS insights_redo_plays_moved AFTER UPDATE ON plays
BEGIN
  UPDATE insight_progress
     SET dirty_from = MIN(COALESCE(dirty_from, added_up_to),
                          date(NEW.started_at, 'unixepoch', 'localtime')),
         dirty_mark = dirty_mark + 1
   WHERE user_id = NEW.user_id
     AND added_up_to >= date(NEW.started_at, 'unixepoch', 'localtime');
END
"""

_REDO_WALLS_IN = """
CREATE TRIGGER IF NOT EXISTS insights_redo_walls_in AFTER INSERT ON theater_sessions
BEGIN
  UPDATE insight_progress
     SET dirty_from = MIN(COALESCE(dirty_from, added_up_to),
                          date(NEW.started_at, 'unixepoch', 'localtime')),
         dirty_mark = dirty_mark + 1
   WHERE user_id = NEW.user_id
     AND added_up_to >= date(NEW.started_at, 'unixepoch', 'localtime');
END
"""

_REDO_WALLS_MOVED = """
CREATE TRIGGER IF NOT EXISTS insights_redo_walls_moved AFTER UPDATE ON theater_sessions
BEGIN
  UPDATE insight_progress
     SET dirty_from = MIN(COALESCE(dirty_from, added_up_to),
                          date(NEW.started_at, 'unixepoch', 'localtime')),
         dirty_mark = dirty_mark + 1
   WHERE user_id = NEW.user_id
     AND added_up_to >= date(NEW.started_at, 'unixepoch', 'localtime');
END
"""

#: A task's run finishing: every User's day it began on (the machine block).
_REDO_RUNS = """
CREATE TRIGGER IF NOT EXISTS insights_redo_runs AFTER UPDATE OF finished_at ON work_runs
BEGIN
  UPDATE insight_progress
     SET dirty_from = MIN(COALESCE(dirty_from, added_up_to),
                          date(NEW.started_at, 'unixepoch', 'localtime')),
         dirty_mark = dirty_mark + 1
   WHERE added_up_to >= date(NEW.started_at, 'unixepoch', 'localtime');
END
"""

#: What a file carries changing: each User's first day with a sitting of it, by the file's own
#: index on `plays`, a probe per User.
_REDO_ASSET_PEOPLE_IN = """
CREATE TRIGGER IF NOT EXISTS insights_redo_asset_people_in AFTER INSERT ON asset_people
BEGIN
  UPDATE insight_progress
     SET dirty_from = MIN(COALESCE(dirty_from, added_up_to), (
           SELECT date(MIN(p.started_at), 'unixepoch', 'localtime') FROM plays p
            WHERE p.asset_id = NEW.asset_id AND p.user_id = insight_progress.user_id)),
         dirty_mark = dirty_mark + 1
   WHERE added_up_to >= (
           SELECT date(MIN(p.started_at), 'unixepoch', 'localtime') FROM plays p
            WHERE p.asset_id = NEW.asset_id AND p.user_id = insight_progress.user_id);
END
"""

_REDO_ASSET_PEOPLE_OUT = """
CREATE TRIGGER IF NOT EXISTS insights_redo_asset_people_out AFTER DELETE ON asset_people
BEGIN
  UPDATE insight_progress
     SET dirty_from = MIN(COALESCE(dirty_from, added_up_to), (
           SELECT date(MIN(p.started_at), 'unixepoch', 'localtime') FROM plays p
            WHERE p.asset_id = OLD.asset_id AND p.user_id = insight_progress.user_id)),
         dirty_mark = dirty_mark + 1
   WHERE added_up_to >= (
           SELECT date(MIN(p.started_at), 'unixepoch', 'localtime') FROM plays p
            WHERE p.asset_id = OLD.asset_id AND p.user_id = insight_progress.user_id);
END
"""

_REDO_ASSET_TAGS_IN = """
CREATE TRIGGER IF NOT EXISTS insights_redo_asset_tags_in AFTER INSERT ON asset_tags
BEGIN
  UPDATE insight_progress
     SET dirty_from = MIN(COALESCE(dirty_from, added_up_to), (
           SELECT date(MIN(p.started_at), 'unixepoch', 'localtime') FROM plays p
            WHERE p.asset_id = NEW.asset_id AND p.user_id = insight_progress.user_id)),
         dirty_mark = dirty_mark + 1
   WHERE added_up_to >= (
           SELECT date(MIN(p.started_at), 'unixepoch', 'localtime') FROM plays p
            WHERE p.asset_id = NEW.asset_id AND p.user_id = insight_progress.user_id);
END
"""

_REDO_ASSET_TAGS_OUT = """
CREATE TRIGGER IF NOT EXISTS insights_redo_asset_tags_out AFTER DELETE ON asset_tags
BEGIN
  UPDATE insight_progress
     SET dirty_from = MIN(COALESCE(dirty_from, added_up_to), (
           SELECT date(MIN(p.started_at), 'unixepoch', 'localtime') FROM plays p
            WHERE p.asset_id = OLD.asset_id AND p.user_id = insight_progress.user_id)),
         dirty_mark = dirty_mark + 1
   WHERE added_up_to >= (
           SELECT date(MIN(p.started_at), 'unixepoch', 'localtime') FROM plays p
            WHERE p.asset_id = OLD.asset_id AND p.user_id = insight_progress.user_id);
END
"""

_REDO_COLLECTION_ITEMS_IN = """
CREATE TRIGGER IF NOT EXISTS insights_redo_collection_items_in AFTER INSERT ON collection_items
BEGIN
  UPDATE insight_progress
     SET dirty_from = MIN(COALESCE(dirty_from, added_up_to), (
           SELECT date(MIN(p.started_at), 'unixepoch', 'localtime') FROM plays p
            WHERE p.asset_id = NEW.asset_id AND p.user_id = insight_progress.user_id)),
         dirty_mark = dirty_mark + 1
   WHERE added_up_to >= (
           SELECT date(MIN(p.started_at), 'unixepoch', 'localtime') FROM plays p
            WHERE p.asset_id = NEW.asset_id AND p.user_id = insight_progress.user_id);
END
"""

_REDO_COLLECTION_ITEMS_OUT = """
CREATE TRIGGER IF NOT EXISTS insights_redo_collection_items_out AFTER DELETE ON collection_items
BEGIN
  UPDATE insight_progress
     SET dirty_from = MIN(COALESCE(dirty_from, added_up_to), (
           SELECT date(MIN(p.started_at), 'unixepoch', 'localtime') FROM plays p
            WHERE p.asset_id = OLD.asset_id AND p.user_id = insight_progress.user_id)),
         dirty_mark = dirty_mark + 1
   WHERE added_up_to >= (
           SELECT date(MIN(p.started_at), 'unixepoch', 'localtime') FROM plays p
            WHERE p.asset_id = OLD.asset_id AND p.user_id = insight_progress.user_id);
END
"""

_REDO_PHOTO_SET_ITEMS_IN = """
CREATE TRIGGER IF NOT EXISTS insights_redo_photo_set_items_in AFTER INSERT ON photo_set_items
BEGIN
  UPDATE insight_progress
     SET dirty_from = MIN(COALESCE(dirty_from, added_up_to), (
           SELECT date(MIN(p.started_at), 'unixepoch', 'localtime') FROM plays p
            WHERE p.asset_id = NEW.asset_id AND p.user_id = insight_progress.user_id)),
         dirty_mark = dirty_mark + 1
   WHERE added_up_to >= (
           SELECT date(MIN(p.started_at), 'unixepoch', 'localtime') FROM plays p
            WHERE p.asset_id = NEW.asset_id AND p.user_id = insight_progress.user_id);
END
"""

_REDO_PHOTO_SET_ITEMS_OUT = """
CREATE TRIGGER IF NOT EXISTS insights_redo_photo_set_items_out AFTER DELETE ON photo_set_items
BEGIN
  UPDATE insight_progress
     SET dirty_from = MIN(COALESCE(dirty_from, added_up_to), (
           SELECT date(MIN(p.started_at), 'unixepoch', 'localtime') FROM plays p
            WHERE p.asset_id = OLD.asset_id AND p.user_id = insight_progress.user_id)),
         dirty_mark = dirty_mark + 1
   WHERE added_up_to >= (
           SELECT date(MIN(p.started_at), 'unixepoch', 'localtime') FROM plays p
            WHERE p.asset_id = OLD.asset_id AND p.user_id = insight_progress.user_id);
END
"""

_REDO_SONG_FILES_IN = """
CREATE TRIGGER IF NOT EXISTS insights_redo_song_files_in AFTER INSERT ON song_files
BEGIN
  UPDATE insight_progress
     SET dirty_from = MIN(COALESCE(dirty_from, added_up_to), (
           SELECT date(MIN(p.started_at), 'unixepoch', 'localtime') FROM plays p
            WHERE p.asset_id = NEW.asset_id AND p.user_id = insight_progress.user_id)),
         dirty_mark = dirty_mark + 1
   WHERE added_up_to >= (
           SELECT date(MIN(p.started_at), 'unixepoch', 'localtime') FROM plays p
            WHERE p.asset_id = NEW.asset_id AND p.user_id = insight_progress.user_id);
END
"""

_REDO_SONG_FILES_OUT = """
CREATE TRIGGER IF NOT EXISTS insights_redo_song_files_out AFTER DELETE ON song_files
BEGIN
  UPDATE insight_progress
     SET dirty_from = MIN(COALESCE(dirty_from, added_up_to), (
           SELECT date(MIN(p.started_at), 'unixepoch', 'localtime') FROM plays p
            WHERE p.asset_id = OLD.asset_id AND p.user_id = insight_progress.user_id)),
         dirty_mark = dirty_mark + 1
   WHERE added_up_to >= (
           SELECT date(MIN(p.started_at), 'unixepoch', 'localtime') FROM plays p
            WHERE p.asset_id = OLD.asset_id AND p.user_id = insight_progress.user_id);
END
"""

#: A file filed under a Username or taken off one: for every User, also the day it arrived, for
#: the arrivals by Site. A one-time question about one file's arrival, asked by the schema.
_REDO_FILED_IN = (
    "CREATE TRIGGER IF NOT EXISTS insights_redo_asset_usernames_in AFTER INSERT ON asset_usernames"
    " BEGIN"
    " UPDATE insight_progress"
    " SET dirty_from = MIN(COALESCE(dirty_from, added_up_to), ("
    " SELECT MIN(d) FROM ("
    " SELECT date(MIN(p.started_at), 'unixepoch', 'localtime') AS d FROM plays p"
    " WHERE p.asset_id = NEW.asset_id"
    " UNION ALL"
    " SELECT date(a.added_at, 'unixepoch', 'localtime') FROM assets a"  # nosemgrep: sift-no-asset-sql-outside-kernel
    " WHERE a.id = NEW.asset_id"
    "))),"
    " dirty_mark = dirty_mark + 1"
    " WHERE added_up_to >= ("
    " SELECT MIN(d) FROM ("
    " SELECT date(MIN(p.started_at), 'unixepoch', 'localtime') AS d FROM plays p"
    " WHERE p.asset_id = NEW.asset_id"
    " UNION ALL"
    " SELECT date(a.added_at, 'unixepoch', 'localtime') FROM assets a"  # nosemgrep: sift-no-asset-sql-outside-kernel
    " WHERE a.id = NEW.asset_id"
    "));"
    " END"
)

_REDO_FILED_OUT = (
    "CREATE TRIGGER IF NOT EXISTS insights_redo_asset_usernames_out AFTER DELETE ON asset_usernames"
    " BEGIN"
    " UPDATE insight_progress"
    " SET dirty_from = MIN(COALESCE(dirty_from, added_up_to), ("
    " SELECT MIN(d) FROM ("
    " SELECT date(MIN(p.started_at), 'unixepoch', 'localtime') AS d FROM plays p"
    " WHERE p.asset_id = OLD.asset_id"
    " UNION ALL"
    " SELECT date(a.added_at, 'unixepoch', 'localtime') FROM assets a"  # nosemgrep: sift-no-asset-sql-outside-kernel
    " WHERE a.id = OLD.asset_id"
    "))),"
    " dirty_mark = dirty_mark + 1"
    " WHERE added_up_to >= ("
    " SELECT MIN(d) FROM ("
    " SELECT date(MIN(p.started_at), 'unixepoch', 'localtime') AS d FROM plays p"
    " WHERE p.asset_id = OLD.asset_id"
    " UNION ALL"
    " SELECT date(a.added_at, 'unixepoch', 'localtime') FROM assets a"  # nosemgrep: sift-no-asset-sql-outside-kernel
    " WHERE a.id = OLD.asset_id"
    "));"
    " END"
)

_REDO = (
    _REDO_PLAYS_IN,
    _REDO_PLAYS_MOVED,
    _REDO_WALLS_IN,
    _REDO_WALLS_MOVED,
    _REDO_RUNS,
    _REDO_ASSET_PEOPLE_IN,
    _REDO_ASSET_PEOPLE_OUT,
    _REDO_ASSET_TAGS_IN,
    _REDO_ASSET_TAGS_OUT,
    _REDO_COLLECTION_ITEMS_IN,
    _REDO_COLLECTION_ITEMS_OUT,
    _REDO_PHOTO_SET_ITEMS_IN,
    _REDO_PHOTO_SET_ITEMS_OUT,
    _REDO_SONG_FILES_IN,
    _REDO_SONG_FILES_OUT,
    _REDO_FILED_IN,
    _REDO_FILED_OUT,
)


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
    if on_disk < 7:
        for table, column, statement in _ADD_V7:
            if not await column_exists(connection, table, column):
                await connection.execute(statement)
        await connection.execute(_RECAPS_MADE_BEFORE)
        # Made again, so it is the newest trigger on the delete and reads the verdict first.
        await connection.execute(_DROP_FILE_DEPARTS)
        await connection.execute(_FILE_DEPARTS)
        await connection.execute(_UNKNOWN_VIEWERS)
        for statement in _REDO:
            await connection.execute(statement)
        await connection.execute(_FORGET_PROGRESS)
    if on_disk < 8 and not await column_exists(connection, "recaps", "left_out"):
        await connection.execute(_ADD_LEFT_OUT)


# `users` comes from the identity component, so it is built before these keys name it. The trigger
# on `assets` reads the verdict, the usernames and the Sites, so the components that make those come
# first; the History the version 6 step reads is asked for rather than required (a database built
# for one feature's tests has none).
register_schema_initializer(
    COMPONENT,
    VERSION,
    initialize,
    depends_on=["identity", "content", "catalog", "visibility", "player", "theater", "ledger"],
    baseline=2,
)
