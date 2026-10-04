# SPDX-License-Identifier: AGPL-3.0-or-later
"""What is counted about a User's day: a closed list, and the one statement that counts each.

## A closed list

A metric is a word a reader asks for and a row is filed under, so it is closed for the reason the
ledger's verbs are: two spellings of one figure would be two figures, and a reader asking for a word
nothing writes would draw a zero that looks like a fact. `METRICS` is the list; `STATEMENTS` holds
one literal statement for each, and a test holds the two equal.

A value is a count or a sum of milliseconds and never anything else, except the two minutes of
the day (`earliest_start`, `latest_finish`), which are the one place a figure is a time of day.

## One statement per metric, written out in full

Every statement is a literal, never assembled from pieces, which is this codebase's rule for SQL
(there is no ORM, so a literal with placeholders is the whole of the injection defence). The price
is that the two shared openings below (the day's views, and the day's sittings) are written out
again in each statement that needs them. That repetition is checked rather than trusted:
`test_metrics.py` holds every copy of each opening identical, so the rule about what a view is and
what a sitting is cannot drift between two metrics.

## What a statement is handed

Every statement takes the same five named values and answers `key, whole, hidden` rows:

* `:user`: whose day.
* `:start`, `:end`: the day as seconds, `[start, end)`, on this device's clock
  (`store.day_bounds`).
* `:views`: a JSON list of the `plays` ids that were VIEWS. Which sittings count is decided once,
  in Python, by the player's own rule (`kernel.content.view_rule`): a statement cannot call
  it, and writing the threshold out again in SQL would be a second copy of the one rule that says
  what a view is. Every figure of what was VIEWED reads the rows those ids name and nothing else
  of `plays`. The one question that reads other rows is WHEN SOMEBODY SAT DOWN (the `k` opening,
  and the finish that follows an evening past midnight): a sitting of any length says somebody
  was there, whether or not it earned a view.
* `:hidden`: a JSON list of the file ids counted as hidden for this User (`store.hidden_files`):
  concealed by the stored verdict, or no longer there to ask about.

## Theater is one hour per hour

A Theater wall of nine cells left running for an hour is nine hours of sittings and one hour of
somebody's evening. The session's own span is what counts towards `viewed_ms` and `sittings`, once,
under the kind `theater`; its cells' sittings are left out of those two and of their `:kind`
breakdown, so the kinds add up to the whole and a stacked bar is honest. The cells DO count for
everything about a THING (the files, people, Sites, Tags, Collections and Photo Sets that were on
the wall), because each of those really was on screen for that long.

A session is hidden when every file its cells showed was hidden; a wall with any file the vault
does not cover is not.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Final

from sift.kernel.access import arrivals
from sift.kernel.access.arrivals import Fetch
from sift.kernel.db import Row

#: How a figure is counted: one statement over the tables this module may read, run with the
#: day's bindings, or, where the figure reads a permission-carrying table, a reader of the
#: access layer handed the same bindings.
Reader = Callable[[Fetch, Mapping[str, Any]], Awaitable[Sequence[Row]]]
Counter = str | Reader

#: Every metric a reader may ask for, and every metric a row may be filed under.
METRICS: Final = frozenset(
    {
        "viewed_ms",
        "viewed_ms:kind",
        "sittings",
        "sittings:kind",
        "files_viewed",
        "files_viewed:kind",
        "viewed_ms:person",
        "files_viewed:person",
        "viewed_ms:site",
        "viewed_ms:tag",
        "viewed_ms:collection",
        "viewed_ms:photo_set",
        "photo_sets_viewed",
        "viewed_ms:song",
        "sittings:file",
        "viewed_ms:hour",
        "viewed_ms:weekday",
        "theater_ms:wall",
        "pickups",
        "first_opened:person",
        "first_opened:kind",
        "earliest_start",
        "latest_finish",
        "rated",
        "rated:file",
        "starred",
        "o",
        "o:file",
        "starred:file",
        "decided",
        "decided:queue",
        "faces_named",
        "files_filed",
        "files_added",
        "files_added:site",
        "files_removed",
        "work_ms:family",
        "faces_found",
        "fingerprints_made",
    }
)

#: The metrics about what SIFT did rather than what the User did: the machine block, admins only.
#: They are the same for every User, and they are added up for every User all the same, because a
#: User's role can change and a day added up without them would be a day that could not be shown.
ADMIN_ONLY: Final = frozenset({"work_ms:family", "faces_found", "fingerprints_made"})

#: The metrics whose value is a minute of the day rather than an amount. Locked, a reader shows
#: `whole - hidden` as for any other metric, which is the figure over what is not hidden; when every
#: sitting that day was hidden the locked figure is 0, and a reader must ask `sittings` whether
#: there was anything to show before it draws one of these.
MINUTES: Final = frozenset({"earliest_start", "latest_finish"})

#: The acts on the faces board that put a name to faces, as its receipts spell them (the faces
#: slice's `NAMED_GROUPS`, `AGREED_WITH_MATCHES`, `AGREED_WITH_PROPOSALS`). Written out in
#: `_FACES_NAMED` because a statement is a literal; `test_metrics.py` holds the two equal.
FACE_NAMING_ACTS: Final = frozenset(
    {"named-groups", "agreed-with-matches", "agreed-with-proposals"}
)

#: How long after a sitting ended the next one has to start to be a new time somebody sat down.
PICKUP_GAP_SECONDS: Final = 30 * 60


# --- the inputs every statement shares --------------------------------------------------------

#: The day's sittings, with what the view rule needs to judge each one: how long it lasted, what
#: kind of file it was and how long that file is: the two facts the sitting wrote down about its
#: file when it began (`plays.kind`, `plays.length_ms`), so a file that is gone, converted, or out
#: of this reader's reach is judged as it was. A sitting from before those were recorded is judged
#: as a video with no length, which is what the rule does for a video whose length was never
#: recorded. Nothing here reads the file itself.
DAY_PLAYS: Final = """
SELECT p.id AS id,
       p.asset_id AS asset_id,
       p.duration_ms AS watched_ms,
       COALESCE(p.kind, 'video') AS media_type,
       p.length_ms AS length_ms
  FROM plays p
 WHERE p.user_id = :user AND p.started_at >= :start AND p.started_at < :end
"""

#: The files the day's opinions were about, so their vault state is asked once with the viewed ones.
DAY_OPINION_FILES: Final = """
SELECT DISTINCT o.subject_id AS asset_id
  FROM opinions o
 WHERE o.user_id = :user AND o.at >= :start AND o.at < :end AND o.subject_kind = 'asset'
"""

#: The files opened in the day after this one: `_LATEST_FINISH` follows the last sitting-down of
#: this day past midnight, and a file opened in that run-on is hidden or not like any of the day's.
#: More than the run-on (the whole next day): asking a file's state it does not end up using
#: costs nothing and leaks nothing.
DAY_RAN_ON_FILES: Final = """
SELECT DISTINCT p.asset_id AS asset_id
  FROM plays p
 WHERE p.user_id = :user AND p.started_at >= :end AND p.started_at < :end + 86400
   AND p.asset_id IS NOT NULL
"""

#: Whether there is anything at all to count for this User on this day among the sources this
#: module reads: one statement, so a User who did nothing costs one read. Everything below reads
#: one of these sources, except the faces found, which cannot be asked by time without walking
#: every face (see `_FACES_FOUND`) and only ever arrive with a task run, which is asked, and the
#: files that arrived, which are the access layer's question (`arrivals.files_added`) and are
#: asked by `count_day` only when this says nothing.
ANYTHING: Final = """
SELECT EXISTS (SELECT 1 FROM plays p
                WHERE p.user_id = :user AND p.started_at >= :start AND p.started_at < :end)
    OR EXISTS (SELECT 1 FROM theater_sessions s
                WHERE s.user_id = :user AND s.started_at >= :start AND s.started_at < :end)
    OR EXISTS (SELECT 1 FROM opinions o
                WHERE o.user_id = :user AND o.at >= :start AND o.at < :end)
    OR EXISTS (SELECT 1 FROM workbench_decisions d
                WHERE d.decided_at >= :start AND d.decided_at < :end)
    OR EXISTS (SELECT 1 FROM work_runs r WHERE r.started_at >= :start AND r.started_at < :end)
    OR EXISTS (SELECT 1 FROM file_departures f WHERE f.ended_at >= :start AND f.ended_at < :end)
    AS anything
"""


# --- the statements ---------------------------------------------------------------------------
#
# Three openings recur, and `test_metrics.py` holds every copy of each identical to the first:
#
#   v is the day's VIEWS: the plays `:views` names, with the file's kind and whether it is hidden.
#   s is the day's SITTINGS: every view that was not a Theater cell, and every Theater session,
#        once, spanning from its start to its end (or, for a wall whose close never arrived, to
#        the last report any of its cells made).
#   k is the day's PICKUPS, the times somebody sat down: every sitting of the day, view or not,
#        and every wall, with nothing (no sitting of any length, no wall) ending in the
#        `PICKUP_GAP_SECONDS` before them. See `_PICKUPS` for why every sitting and not the views.

_VIEWED_MS = """
WITH v AS (
  SELECT p.id, p.asset_id, p.duration_ms AS ms, p.started_at,
         MAX(p.made_at, p.started_at + p.duration_ms / 1000) AS ended_at,
         COALESCE(p.kind, 'video') AS kind,
         COALESCE(p.screen, '') = 'theater' AS in_theater, p.theater_session,
         p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
    FROM plays p
   WHERE p.id IN (SELECT value FROM json_each(:views)) AND p.user_id = :user
),
s AS (
  SELECT kind, ms, hid, started_at, ended_at, asset_id FROM v WHERE NOT in_theater
  UNION ALL
  SELECT 'theater', w.ms, w.hid, w.started_at, w.started_at + w.ms / 1000, NULL
    FROM (SELECT t.started_at,
                 MAX(0, COALESCE(t.ended_at,
                                 (SELECT MAX(q.made_at) FROM plays q
                                   WHERE q.user_id = :user AND q.theater_session = t.session),
                                 t.started_at) - t.started_at) * 1000 AS ms,
                 EXISTS (SELECT 1 FROM v WHERE v.theater_session = t.session)
                 AND NOT EXISTS (SELECT 1 FROM v
                                  WHERE v.theater_session = t.session AND NOT v.hid) AS hid
            FROM theater_sessions t
           WHERE t.user_id = :user AND t.started_at >= :start AND t.started_at < :end) w
)
SELECT '' AS key, SUM(ms) AS whole, SUM(ms * hid) AS hidden FROM s HAVING COUNT(*) > 0
"""

_VIEWED_MS_KIND = """
WITH v AS (
  SELECT p.id, p.asset_id, p.duration_ms AS ms, p.started_at,
         MAX(p.made_at, p.started_at + p.duration_ms / 1000) AS ended_at,
         COALESCE(p.kind, 'video') AS kind,
         COALESCE(p.screen, '') = 'theater' AS in_theater, p.theater_session,
         p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
    FROM plays p
   WHERE p.id IN (SELECT value FROM json_each(:views)) AND p.user_id = :user
),
s AS (
  SELECT kind, ms, hid, started_at, ended_at, asset_id FROM v WHERE NOT in_theater
  UNION ALL
  SELECT 'theater', w.ms, w.hid, w.started_at, w.started_at + w.ms / 1000, NULL
    FROM (SELECT t.started_at,
                 MAX(0, COALESCE(t.ended_at,
                                 (SELECT MAX(q.made_at) FROM plays q
                                   WHERE q.user_id = :user AND q.theater_session = t.session),
                                 t.started_at) - t.started_at) * 1000 AS ms,
                 EXISTS (SELECT 1 FROM v WHERE v.theater_session = t.session)
                 AND NOT EXISTS (SELECT 1 FROM v
                                  WHERE v.theater_session = t.session AND NOT v.hid) AS hid
            FROM theater_sessions t
           WHERE t.user_id = :user AND t.started_at >= :start AND t.started_at < :end) w
)
SELECT kind AS key, SUM(ms) AS whole, SUM(ms * hid) AS hidden FROM s GROUP BY kind
"""

_SITTINGS = """
WITH v AS (
  SELECT p.id, p.asset_id, p.duration_ms AS ms, p.started_at,
         MAX(p.made_at, p.started_at + p.duration_ms / 1000) AS ended_at,
         COALESCE(p.kind, 'video') AS kind,
         COALESCE(p.screen, '') = 'theater' AS in_theater, p.theater_session,
         p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
    FROM plays p
   WHERE p.id IN (SELECT value FROM json_each(:views)) AND p.user_id = :user
),
s AS (
  SELECT kind, ms, hid, started_at, ended_at, asset_id FROM v WHERE NOT in_theater
  UNION ALL
  SELECT 'theater', w.ms, w.hid, w.started_at, w.started_at + w.ms / 1000, NULL
    FROM (SELECT t.started_at,
                 MAX(0, COALESCE(t.ended_at,
                                 (SELECT MAX(q.made_at) FROM plays q
                                   WHERE q.user_id = :user AND q.theater_session = t.session),
                                 t.started_at) - t.started_at) * 1000 AS ms,
                 EXISTS (SELECT 1 FROM v WHERE v.theater_session = t.session)
                 AND NOT EXISTS (SELECT 1 FROM v
                                  WHERE v.theater_session = t.session AND NOT v.hid) AS hid
            FROM theater_sessions t
           WHERE t.user_id = :user AND t.started_at >= :start AND t.started_at < :end) w
)
SELECT '' AS key, COUNT(*) AS whole, SUM(hid) AS hidden FROM s HAVING COUNT(*) > 0
"""

_SITTINGS_KIND = """
WITH v AS (
  SELECT p.id, p.asset_id, p.duration_ms AS ms, p.started_at,
         MAX(p.made_at, p.started_at + p.duration_ms / 1000) AS ended_at,
         COALESCE(p.kind, 'video') AS kind,
         COALESCE(p.screen, '') = 'theater' AS in_theater, p.theater_session,
         p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
    FROM plays p
   WHERE p.id IN (SELECT value FROM json_each(:views)) AND p.user_id = :user
),
s AS (
  SELECT kind, ms, hid, started_at, ended_at, asset_id FROM v WHERE NOT in_theater
  UNION ALL
  SELECT 'theater', w.ms, w.hid, w.started_at, w.started_at + w.ms / 1000, NULL
    FROM (SELECT t.started_at,
                 MAX(0, COALESCE(t.ended_at,
                                 (SELECT MAX(q.made_at) FROM plays q
                                   WHERE q.user_id = :user AND q.theater_session = t.session),
                                 t.started_at) - t.started_at) * 1000 AS ms,
                 EXISTS (SELECT 1 FROM v WHERE v.theater_session = t.session)
                 AND NOT EXISTS (SELECT 1 FROM v
                                  WHERE v.theater_session = t.session AND NOT v.hid) AS hid
            FROM theater_sessions t
           WHERE t.user_id = :user AND t.started_at >= :start AND t.started_at < :end) w
)
SELECT kind AS key, COUNT(*) AS whole, SUM(hid) AS hidden FROM s GROUP BY kind
"""

_FILES_VIEWED = """
WITH v AS (
  SELECT p.id, p.asset_id, p.duration_ms AS ms, p.started_at,
         MAX(p.made_at, p.started_at + p.duration_ms / 1000) AS ended_at,
         COALESCE(p.kind, 'video') AS kind,
         COALESCE(p.screen, '') = 'theater' AS in_theater, p.theater_session,
         p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
    FROM plays p
   WHERE p.id IN (SELECT value FROM json_each(:views)) AND p.user_id = :user
)
SELECT '' AS key, COUNT(DISTINCT asset_id) AS whole,
       COUNT(DISTINCT CASE WHEN hid THEN asset_id END) AS hidden
  FROM v WHERE asset_id IS NOT NULL HAVING COUNT(*) > 0
"""

_FILES_VIEWED_KIND = """
WITH v AS (
  SELECT p.id, p.asset_id, p.duration_ms AS ms, p.started_at,
         MAX(p.made_at, p.started_at + p.duration_ms / 1000) AS ended_at,
         COALESCE(p.kind, 'video') AS kind,
         COALESCE(p.screen, '') = 'theater' AS in_theater, p.theater_session,
         p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
    FROM plays p
   WHERE p.id IN (SELECT value FROM json_each(:views)) AND p.user_id = :user
)
SELECT kind AS key, COUNT(DISTINCT asset_id) AS whole,
       COUNT(DISTINCT CASE WHEN hid THEN asset_id END) AS hidden
  FROM v WHERE asset_id IS NOT NULL GROUP BY kind
"""

_VIEWED_MS_PERSON = """
WITH v AS (
  SELECT p.id, p.asset_id, p.duration_ms AS ms, p.started_at,
         MAX(p.made_at, p.started_at + p.duration_ms / 1000) AS ended_at,
         COALESCE(p.kind, 'video') AS kind,
         COALESCE(p.screen, '') = 'theater' AS in_theater, p.theater_session,
         p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
    FROM plays p
   WHERE p.id IN (SELECT value FROM json_each(:views)) AND p.user_id = :user
)
SELECT x.person_id AS key, SUM(v.ms) AS whole,
       CASE WHEN EXISTS (SELECT 1 FROM person_user_state h
                          WHERE h.user_id = :user AND h.person_id = x.person_id AND h.hidden = 1)
            THEN SUM(v.ms) ELSE SUM(v.ms * v.hid) END AS hidden
  FROM v JOIN asset_people x ON x.asset_id = v.asset_id
 GROUP BY x.person_id
"""

_FILES_VIEWED_PERSON = """
WITH v AS (
  SELECT p.id, p.asset_id, p.duration_ms AS ms, p.started_at,
         MAX(p.made_at, p.started_at + p.duration_ms / 1000) AS ended_at,
         COALESCE(p.kind, 'video') AS kind,
         COALESCE(p.screen, '') = 'theater' AS in_theater, p.theater_session,
         p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
    FROM plays p
   WHERE p.id IN (SELECT value FROM json_each(:views)) AND p.user_id = :user
)
SELECT x.person_id AS key, COUNT(DISTINCT v.asset_id) AS whole,
       CASE WHEN EXISTS (SELECT 1 FROM person_user_state h
                          WHERE h.user_id = :user AND h.person_id = x.person_id AND h.hidden = 1)
            THEN COUNT(DISTINCT v.asset_id)
            ELSE COUNT(DISTINCT CASE WHEN v.hid THEN v.asset_id END) END AS hidden
  FROM v JOIN asset_people x ON x.asset_id = v.asset_id
 GROUP BY x.person_id
"""

# A file under two Usernames of one Site is one file of that Site: the pairs are made distinct
# before they are joined, or its time would count twice.
_VIEWED_MS_SITE = """
WITH v AS (
  SELECT p.id, p.asset_id, p.duration_ms AS ms, p.started_at,
         MAX(p.made_at, p.started_at + p.duration_ms / 1000) AS ended_at,
         COALESCE(p.kind, 'video') AS kind,
         COALESCE(p.screen, '') = 'theater' AS in_theater, p.theater_session,
         p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
    FROM plays p
   WHERE p.id IN (SELECT value FROM json_each(:views)) AND p.user_id = :user
),
x AS (
  SELECT DISTINCT au.asset_id, u.site_id
    FROM asset_usernames au JOIN usernames u ON u.id = au.username_id
   WHERE au.asset_id IN (SELECT asset_id FROM v)
)
SELECT x.site_id AS key, SUM(v.ms) AS whole,
       CASE WHEN EXISTS (SELECT 1 FROM site_user_state h
                          WHERE h.user_id = :user AND h.site_id = x.site_id AND h.hidden = 1)
            THEN SUM(v.ms) ELSE SUM(v.ms * v.hid) END AS hidden
  FROM v JOIN x ON x.asset_id = v.asset_id
 GROUP BY x.site_id
"""

_VIEWED_MS_TAG = """
WITH v AS (
  SELECT p.id, p.asset_id, p.duration_ms AS ms, p.started_at,
         MAX(p.made_at, p.started_at + p.duration_ms / 1000) AS ended_at,
         COALESCE(p.kind, 'video') AS kind,
         COALESCE(p.screen, '') = 'theater' AS in_theater, p.theater_session,
         p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
    FROM plays p
   WHERE p.id IN (SELECT value FROM json_each(:views)) AND p.user_id = :user
)
SELECT x.tag_id AS key, SUM(v.ms) AS whole,
       CASE WHEN EXISTS (SELECT 1 FROM tag_user_state h
                          WHERE h.user_id = :user AND h.tag_id = x.tag_id AND h.hidden = 1)
            THEN SUM(v.ms) ELSE SUM(v.ms * v.hid) END AS hidden
  FROM v JOIN asset_tags x ON x.asset_id = v.asset_id
 GROUP BY x.tag_id
"""

_VIEWED_MS_COLLECTION = """
WITH v AS (
  SELECT p.id, p.asset_id, p.duration_ms AS ms, p.started_at,
         MAX(p.made_at, p.started_at + p.duration_ms / 1000) AS ended_at,
         COALESCE(p.kind, 'video') AS kind,
         COALESCE(p.screen, '') = 'theater' AS in_theater, p.theater_session,
         p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
    FROM plays p
   WHERE p.id IN (SELECT value FROM json_each(:views)) AND p.user_id = :user
)
SELECT x.collection_id AS key, SUM(v.ms) AS whole,
       CASE WHEN EXISTS (SELECT 1 FROM collection_user_state h
                          WHERE h.user_id = :user AND h.collection_id = x.collection_id
                            AND h.hidden = 1)
            THEN SUM(v.ms) ELSE SUM(v.ms * v.hid) END AS hidden
  FROM v JOIN collection_items x ON x.asset_id = v.asset_id
 GROUP BY x.collection_id
"""

# A Photo Set has no hidden state of its own for a User: the vault reaches it through its files.
_VIEWED_MS_PHOTO_SET = """
WITH v AS (
  SELECT p.id, p.asset_id, p.duration_ms AS ms, p.started_at,
         MAX(p.made_at, p.started_at + p.duration_ms / 1000) AS ended_at,
         COALESCE(p.kind, 'video') AS kind,
         COALESCE(p.screen, '') = 'theater' AS in_theater, p.theater_session,
         p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
    FROM plays p
   WHERE p.id IN (SELECT value FROM json_each(:views)) AND p.user_id = :user
)
SELECT x.photo_set_id AS key, SUM(v.ms) AS whole, SUM(v.ms * v.hid) AS hidden
  FROM v JOIN photo_set_items x ON x.asset_id = v.asset_id
 GROUP BY x.photo_set_id
"""

# A song has no hidden state of its own for a User either: it is seen through its files, so the
# part of its time that is hidden is the time spent on its hidden files.
_VIEWED_MS_SONG = """
WITH v AS (
  SELECT p.id, p.asset_id, p.duration_ms AS ms, p.started_at,
         MAX(p.made_at, p.started_at + p.duration_ms / 1000) AS ended_at,
         COALESCE(p.kind, 'video') AS kind,
         COALESCE(p.screen, '') = 'theater' AS in_theater, p.theater_session,
         p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
    FROM plays p
   WHERE p.id IN (SELECT value FROM json_each(:views)) AND p.user_id = :user
)
SELECT x.song_id AS key, SUM(v.ms) AS whole, SUM(v.ms * v.hid) AS hidden
  FROM v JOIN song_files x ON x.asset_id = v.asset_id
 GROUP BY x.song_id
"""

# A Photo Set counts as hidden when every file of it viewed that day was hidden.
_PHOTO_SETS_VIEWED = """
WITH v AS (
  SELECT p.id, p.asset_id, p.duration_ms AS ms, p.started_at,
         MAX(p.made_at, p.started_at + p.duration_ms / 1000) AS ended_at,
         COALESCE(p.kind, 'video') AS kind,
         COALESCE(p.screen, '') = 'theater' AS in_theater, p.theater_session,
         p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
    FROM plays p
   WHERE p.id IN (SELECT value FROM json_each(:views)) AND p.user_id = :user
)
SELECT '' AS key, COUNT(*) AS whole, SUM(all_hidden) AS hidden
  FROM (SELECT x.photo_set_id, MIN(v.hid) AS all_hidden
          FROM v JOIN photo_set_items x ON x.asset_id = v.asset_id
         GROUP BY x.photo_set_id)
HAVING COUNT(*) > 0
"""

# Keyed `<kind>:<asset id>`, the kind the sitting wrote down: a period's files are the distinct
# keys over its days, and the split by kind is read off the key, so a file deleted since it was
# viewed still counts as the video or picture it was (`split_file_key`).
_SITTINGS_FILE = """
WITH v AS (
  SELECT p.id, p.asset_id, p.duration_ms AS ms, p.started_at,
         MAX(p.made_at, p.started_at + p.duration_ms / 1000) AS ended_at,
         COALESCE(p.kind, 'video') AS kind,
         COALESCE(p.screen, '') = 'theater' AS in_theater, p.theater_session,
         p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
    FROM plays p
   WHERE p.id IN (SELECT value FROM json_each(:views)) AND p.user_id = :user
)
SELECT kind || ':' || asset_id AS key, COUNT(*) AS whole, SUM(hid) AS hidden
  FROM v WHERE asset_id IS NOT NULL GROUP BY kind, asset_id
"""

# The hours of this device's clock a sitting's time fell in, each given its own part of it. The
# time is laid from the moment the sitting started (`x.top` is the start of that clock hour), so
# an evening in Theater from 22:00 to 01:00 is an hour in each of 22, 23 and 00, never three hours
# in 22: a bar of one hour holding three hours of viewing would be a figure no clock can hold,
# and the busiest hour would read "began at 10:00 PM: 3 hours". Twenty-four slots, the last taking whatever is
# left, so the parts always add up to the sitting's whole time. The slot is named by its own local
# hour, so a day the clocks change on names every slot as the clock on the wall did.
_VIEWED_MS_HOUR = """
WITH v AS (
  SELECT p.id, p.asset_id, p.duration_ms AS ms, p.started_at,
         MAX(p.made_at, p.started_at + p.duration_ms / 1000) AS ended_at,
         COALESCE(p.kind, 'video') AS kind,
         COALESCE(p.screen, '') = 'theater' AS in_theater, p.theater_session,
         p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
    FROM plays p
   WHERE p.id IN (SELECT value FROM json_each(:views)) AND p.user_id = :user
),
s AS (
  SELECT kind, ms, hid, started_at, ended_at, asset_id FROM v WHERE NOT in_theater
  UNION ALL
  SELECT 'theater', w.ms, w.hid, w.started_at, w.started_at + w.ms / 1000, NULL
    FROM (SELECT t.started_at,
                 MAX(0, COALESCE(t.ended_at,
                                 (SELECT MAX(q.made_at) FROM plays q
                                   WHERE q.user_id = :user AND q.theater_session = t.session),
                                 t.started_at) - t.started_at) * 1000 AS ms,
                 EXISTS (SELECT 1 FROM v WHERE v.theater_session = t.session)
                 AND NOT EXISTS (SELECT 1 FROM v
                                  WHERE v.theater_session = t.session AND NOT v.hid) AS hid
            FROM theater_sessions t
           WHERE t.user_id = :user AND t.started_at >= :start AND t.started_at < :end) w
),
h(n) AS (VALUES (0), (1), (2), (3), (4), (5), (6), (7), (8), (9), (10), (11), (12), (13),
                (14), (15), (16), (17), (18), (19), (20), (21), (22), (23)),
x AS (
  SELECT hid, started_at * 1000 AS a, started_at * 1000 + ms AS b,
         started_at
         - CAST(strftime('%M', started_at, 'unixepoch', 'localtime') AS INTEGER) * 60
         - CAST(strftime('%S', started_at, 'unixepoch', 'localtime') AS INTEGER) AS top
    FROM s WHERE ms > 0
),
y AS (
  SELECT x.hid, x.top + 3600 * h.n AS slot,
         CASE WHEN h.n = 23 THEN x.b
              ELSE MIN(x.b, (x.top + 3600 * (h.n + 1)) * 1000) END
         - MAX(x.a, (x.top + 3600 * h.n) * 1000) AS ms
    FROM x JOIN h ON (x.top + 3600 * h.n) * 1000 < x.b
)
SELECT strftime('%H', slot, 'unixepoch', 'localtime') AS key,
       SUM(ms) AS whole, SUM(ms * hid) AS hidden
  FROM y GROUP BY key
"""

# Monday is 0, as Python counts; SQLite's `%w` counts from Sunday.
_VIEWED_MS_WEEKDAY = """
WITH v AS (
  SELECT p.id, p.asset_id, p.duration_ms AS ms, p.started_at,
         MAX(p.made_at, p.started_at + p.duration_ms / 1000) AS ended_at,
         COALESCE(p.kind, 'video') AS kind,
         COALESCE(p.screen, '') = 'theater' AS in_theater, p.theater_session,
         p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
    FROM plays p
   WHERE p.id IN (SELECT value FROM json_each(:views)) AND p.user_id = :user
),
s AS (
  SELECT kind, ms, hid, started_at, ended_at, asset_id FROM v WHERE NOT in_theater
  UNION ALL
  SELECT 'theater', w.ms, w.hid, w.started_at, w.started_at + w.ms / 1000, NULL
    FROM (SELECT t.started_at,
                 MAX(0, COALESCE(t.ended_at,
                                 (SELECT MAX(q.made_at) FROM plays q
                                   WHERE q.user_id = :user AND q.theater_session = t.session),
                                 t.started_at) - t.started_at) * 1000 AS ms,
                 EXISTS (SELECT 1 FROM v WHERE v.theater_session = t.session)
                 AND NOT EXISTS (SELECT 1 FROM v
                                  WHERE v.theater_session = t.session AND NOT v.hid) AS hid
            FROM theater_sessions t
           WHERE t.user_id = :user AND t.started_at >= :start AND t.started_at < :end) w
)
SELECT CAST((CAST(strftime('%w', started_at, 'unixepoch', 'localtime') AS INTEGER) + 6) % 7
            AS TEXT) AS key,
       SUM(ms) AS whole, SUM(ms * hid) AS hidden
  FROM s GROUP BY key
"""

_THEATER_MS_WALL = """
WITH v AS (
  SELECT p.id, p.asset_id, p.duration_ms AS ms, p.started_at,
         MAX(p.made_at, p.started_at + p.duration_ms / 1000) AS ended_at,
         COALESCE(p.kind, 'video') AS kind,
         COALESCE(p.screen, '') = 'theater' AS in_theater, p.theater_session,
         p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
    FROM plays p
   WHERE p.id IN (SELECT value FROM json_each(:views)) AND p.user_id = :user
)
SELECT w.wall AS key, SUM(w.ms) AS whole, SUM(w.ms * w.hid) AS hidden
  FROM (SELECT COALESCE(t.arrangement_id, '') AS wall,
               MAX(0, COALESCE(t.ended_at,
                               (SELECT MAX(q.made_at) FROM plays q
                                 WHERE q.user_id = :user AND q.theater_session = t.session),
                               t.started_at) - t.started_at) * 1000 AS ms,
               EXISTS (SELECT 1 FROM v WHERE v.theater_session = t.session)
               AND NOT EXISTS (SELECT 1 FROM v
                                WHERE v.theater_session = t.session AND NOT v.hid) AS hid
          FROM theater_sessions t
         WHERE t.user_id = :user AND t.started_at >= :start AND t.started_at < :end) w
 GROUP BY w.wall
"""

# A sitting is a new time somebody sat down when nothing (no sitting of any length, no Theater
# wall) ended in the half hour before it started. The look back reaches into the day before, so
# the first sitting after midnight is judged exactly as any other.
#
# The CANDIDATES are every sitting too, not only the views (`k` reads the day's `plays` rows, the
# one opening that does): the look back counts a sitting of any length as somebody being there, so
# a candidate list of views alone would never count a sitting-down that began with a file skipped
# past: its first view has that skip in the half hour before it, and the skip is no candidate. A
# day of eight views after a two-second skip would read "opened Sift 0 times" and have no earliest
# start.
_PICKUPS = """
WITH v AS (
  SELECT p.id, p.asset_id, p.duration_ms AS ms, p.started_at,
         MAX(p.made_at, p.started_at + p.duration_ms / 1000) AS ended_at,
         COALESCE(p.kind, 'video') AS kind,
         COALESCE(p.screen, '') = 'theater' AS in_theater, p.theater_session,
         p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
    FROM plays p
   WHERE p.id IN (SELECT value FROM json_each(:views)) AND p.user_id = :user
),
s AS (
  SELECT kind, ms, hid, started_at, ended_at, asset_id FROM v WHERE NOT in_theater
  UNION ALL
  SELECT 'theater', w.ms, w.hid, w.started_at, w.started_at + w.ms / 1000, NULL
    FROM (SELECT t.started_at,
                 MAX(0, COALESCE(t.ended_at,
                                 (SELECT MAX(q.made_at) FROM plays q
                                   WHERE q.user_id = :user AND q.theater_session = t.session),
                                 t.started_at) - t.started_at) * 1000 AS ms,
                 EXISTS (SELECT 1 FROM v WHERE v.theater_session = t.session)
                 AND NOT EXISTS (SELECT 1 FROM v
                                  WHERE v.theater_session = t.session AND NOT v.hid) AS hid
            FROM theater_sessions t
           WHERE t.user_id = :user AND t.started_at >= :start AND t.started_at < :end) w
),
k AS (
  SELECT c.kind, c.hid, c.asset_id, c.started_at
    FROM (SELECT COALESCE(p.kind, 'video') AS kind,
                 p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid,
                 p.asset_id, p.started_at
            FROM plays p
           WHERE p.user_id = :user AND p.started_at >= :start AND p.started_at < :end
             AND COALESCE(p.screen, '') <> 'theater'
          UNION ALL
          SELECT kind, hid, asset_id, started_at FROM s WHERE kind = 'theater') c
   WHERE NOT EXISTS (SELECT 1 FROM plays q
                      WHERE q.user_id = :user
                        AND q.started_at < c.started_at AND q.started_at >= c.started_at - 86400
                        AND MAX(q.made_at, q.started_at + q.duration_ms / 1000)
                            > c.started_at - 1800)
     AND NOT EXISTS (SELECT 1 FROM theater_sessions r
                      WHERE r.user_id = :user
                        AND r.started_at < c.started_at AND r.started_at >= c.started_at - 86400
                        AND COALESCE(r.ended_at, r.started_at) > c.started_at - 1800)
)
SELECT '' AS key, COUNT(*) AS whole, SUM(hid) AS hidden FROM k HAVING COUNT(*) > 0
"""

_FIRST_OPENED_PERSON = """
WITH v AS (
  SELECT p.id, p.asset_id, p.duration_ms AS ms, p.started_at,
         MAX(p.made_at, p.started_at + p.duration_ms / 1000) AS ended_at,
         COALESCE(p.kind, 'video') AS kind,
         COALESCE(p.screen, '') = 'theater' AS in_theater, p.theater_session,
         p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
    FROM plays p
   WHERE p.id IN (SELECT value FROM json_each(:views)) AND p.user_id = :user
),
s AS (
  SELECT kind, ms, hid, started_at, ended_at, asset_id FROM v WHERE NOT in_theater
  UNION ALL
  SELECT 'theater', w.ms, w.hid, w.started_at, w.started_at + w.ms / 1000, NULL
    FROM (SELECT t.started_at,
                 MAX(0, COALESCE(t.ended_at,
                                 (SELECT MAX(q.made_at) FROM plays q
                                   WHERE q.user_id = :user AND q.theater_session = t.session),
                                 t.started_at) - t.started_at) * 1000 AS ms,
                 EXISTS (SELECT 1 FROM v WHERE v.theater_session = t.session)
                 AND NOT EXISTS (SELECT 1 FROM v
                                  WHERE v.theater_session = t.session AND NOT v.hid) AS hid
            FROM theater_sessions t
           WHERE t.user_id = :user AND t.started_at >= :start AND t.started_at < :end) w
),
k AS (
  SELECT c.kind, c.hid, c.asset_id, c.started_at
    FROM (SELECT COALESCE(p.kind, 'video') AS kind,
                 p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid,
                 p.asset_id, p.started_at
            FROM plays p
           WHERE p.user_id = :user AND p.started_at >= :start AND p.started_at < :end
             AND COALESCE(p.screen, '') <> 'theater'
          UNION ALL
          SELECT kind, hid, asset_id, started_at FROM s WHERE kind = 'theater') c
   WHERE NOT EXISTS (SELECT 1 FROM plays q
                      WHERE q.user_id = :user
                        AND q.started_at < c.started_at AND q.started_at >= c.started_at - 86400
                        AND MAX(q.made_at, q.started_at + q.duration_ms / 1000)
                            > c.started_at - 1800)
     AND NOT EXISTS (SELECT 1 FROM theater_sessions r
                      WHERE r.user_id = :user
                        AND r.started_at < c.started_at AND r.started_at >= c.started_at - 86400
                        AND COALESCE(r.ended_at, r.started_at) > c.started_at - 1800)
)
SELECT x.person_id AS key, COUNT(*) AS whole,
       CASE WHEN EXISTS (SELECT 1 FROM person_user_state h
                          WHERE h.user_id = :user AND h.person_id = x.person_id AND h.hidden = 1)
            THEN COUNT(*) ELSE SUM(k.hid) END AS hidden
  FROM k JOIN asset_people x ON x.asset_id = k.asset_id
 GROUP BY x.person_id
"""

_FIRST_OPENED_KIND = """
WITH v AS (
  SELECT p.id, p.asset_id, p.duration_ms AS ms, p.started_at,
         MAX(p.made_at, p.started_at + p.duration_ms / 1000) AS ended_at,
         COALESCE(p.kind, 'video') AS kind,
         COALESCE(p.screen, '') = 'theater' AS in_theater, p.theater_session,
         p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
    FROM plays p
   WHERE p.id IN (SELECT value FROM json_each(:views)) AND p.user_id = :user
),
s AS (
  SELECT kind, ms, hid, started_at, ended_at, asset_id FROM v WHERE NOT in_theater
  UNION ALL
  SELECT 'theater', w.ms, w.hid, w.started_at, w.started_at + w.ms / 1000, NULL
    FROM (SELECT t.started_at,
                 MAX(0, COALESCE(t.ended_at,
                                 (SELECT MAX(q.made_at) FROM plays q
                                   WHERE q.user_id = :user AND q.theater_session = t.session),
                                 t.started_at) - t.started_at) * 1000 AS ms,
                 EXISTS (SELECT 1 FROM v WHERE v.theater_session = t.session)
                 AND NOT EXISTS (SELECT 1 FROM v
                                  WHERE v.theater_session = t.session AND NOT v.hid) AS hid
            FROM theater_sessions t
           WHERE t.user_id = :user AND t.started_at >= :start AND t.started_at < :end) w
),
k AS (
  SELECT c.kind, c.hid, c.asset_id, c.started_at
    FROM (SELECT COALESCE(p.kind, 'video') AS kind,
                 p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid,
                 p.asset_id, p.started_at
            FROM plays p
           WHERE p.user_id = :user AND p.started_at >= :start AND p.started_at < :end
             AND COALESCE(p.screen, '') <> 'theater'
          UNION ALL
          SELECT kind, hid, asset_id, started_at FROM s WHERE kind = 'theater') c
   WHERE NOT EXISTS (SELECT 1 FROM plays q
                      WHERE q.user_id = :user
                        AND q.started_at < c.started_at AND q.started_at >= c.started_at - 86400
                        AND MAX(q.made_at, q.started_at + q.duration_ms / 1000)
                            > c.started_at - 1800)
     AND NOT EXISTS (SELECT 1 FROM theater_sessions r
                      WHERE r.user_id = :user
                        AND r.started_at < c.started_at AND r.started_at >= c.started_at - 86400
                        AND COALESCE(r.ended_at, r.started_at) > c.started_at - 1800)
)
SELECT kind AS key, COUNT(*) AS whole, SUM(hid) AS hidden FROM k GROUP BY kind
"""

# Minutes of the local day. `whole - hidden` is the minute over what is not hidden (see
# `MINUTES`), so `hidden` is the difference between the two, which is negative for the earliest
# start whenever the first sitting of the day was a hidden one.
#
# BOTH ARE ABOUT A TIME SOMEBODY SAT DOWN (`k`, the pickups' own rule), never about one file opened
# inside one. An evening that runs past midnight goes on opening files after it, and counted per
# file the next day's "earliest start" would be 00:00 (the middle of last night) on every day of a
# night owl's month, and a week would read "earliest start 00:00, latest finish 00:11" on one
# date. So
# the start is the first sitting-down that BEGAN this day, and the finish is when the last one that
# began this day ended, followed past midnight until the next time somebody sat down: the evening
# belongs to the day it started in, whole. A day whose only viewing ran on from the night before
# began nothing, and has neither.
_EARLIEST_START = """
WITH v AS (
  SELECT p.id, p.asset_id, p.duration_ms AS ms, p.started_at,
         MAX(p.made_at, p.started_at + p.duration_ms / 1000) AS ended_at,
         COALESCE(p.kind, 'video') AS kind,
         COALESCE(p.screen, '') = 'theater' AS in_theater, p.theater_session,
         p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
    FROM plays p
   WHERE p.id IN (SELECT value FROM json_each(:views)) AND p.user_id = :user
),
s AS (
  SELECT kind, ms, hid, started_at, ended_at, asset_id FROM v WHERE NOT in_theater
  UNION ALL
  SELECT 'theater', w.ms, w.hid, w.started_at, w.started_at + w.ms / 1000, NULL
    FROM (SELECT t.started_at,
                 MAX(0, COALESCE(t.ended_at,
                                 (SELECT MAX(q.made_at) FROM plays q
                                   WHERE q.user_id = :user AND q.theater_session = t.session),
                                 t.started_at) - t.started_at) * 1000 AS ms,
                 EXISTS (SELECT 1 FROM v WHERE v.theater_session = t.session)
                 AND NOT EXISTS (SELECT 1 FROM v
                                  WHERE v.theater_session = t.session AND NOT v.hid) AS hid
            FROM theater_sessions t
           WHERE t.user_id = :user AND t.started_at >= :start AND t.started_at < :end) w
),
k AS (
  SELECT c.kind, c.hid, c.asset_id, c.started_at
    FROM (SELECT COALESCE(p.kind, 'video') AS kind,
                 p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid,
                 p.asset_id, p.started_at
            FROM plays p
           WHERE p.user_id = :user AND p.started_at >= :start AND p.started_at < :end
             AND COALESCE(p.screen, '') <> 'theater'
          UNION ALL
          SELECT kind, hid, asset_id, started_at FROM s WHERE kind = 'theater') c
   WHERE NOT EXISTS (SELECT 1 FROM plays q
                      WHERE q.user_id = :user
                        AND q.started_at < c.started_at AND q.started_at >= c.started_at - 86400
                        AND MAX(q.made_at, q.started_at + q.duration_ms / 1000)
                            > c.started_at - 1800)
     AND NOT EXISTS (SELECT 1 FROM theater_sessions r
                      WHERE r.user_id = :user
                        AND r.started_at < c.started_at AND r.started_at >= c.started_at - 86400
                        AND COALESCE(r.ended_at, r.started_at) > c.started_at - 1800)
),
m AS (
  SELECT hid,
         CAST(strftime('%H', started_at, 'unixepoch', 'localtime') AS INTEGER) * 60
         + CAST(strftime('%M', started_at, 'unixepoch', 'localtime') AS INTEGER) AS minute
    FROM k
)
SELECT '' AS key, MIN(minute) AS whole,
       MIN(minute) - COALESCE(MIN(CASE WHEN NOT hid THEN minute END), 0) AS hidden
  FROM m HAVING COUNT(*) > 0
"""

# Past midnight the minute keeps counting (1440 is midnight at the end of this day), because the
# evening that ran late belongs to the day it started in. `f` is the first sitting-down of this day
# (what came before it ran on from the night before, and is that day's); `n` is the next time
# somebody sat down after this day ended, found by the same rule over the raw sittings, as the
# pickups' look back reads them. Everything opened from `f` up to `n` is this day's evening, and
# its files' vault state is asked with the day's own (`DAY_RAN_ON_FILES`).
_LATEST_FINISH = """
WITH v AS (
  SELECT p.id, p.asset_id, p.duration_ms AS ms, p.started_at,
         MAX(p.made_at, p.started_at + p.duration_ms / 1000) AS ended_at,
         COALESCE(p.kind, 'video') AS kind,
         COALESCE(p.screen, '') = 'theater' AS in_theater, p.theater_session,
         p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
    FROM plays p
   WHERE p.id IN (SELECT value FROM json_each(:views)) AND p.user_id = :user
),
s AS (
  SELECT kind, ms, hid, started_at, ended_at, asset_id FROM v WHERE NOT in_theater
  UNION ALL
  SELECT 'theater', w.ms, w.hid, w.started_at, w.started_at + w.ms / 1000, NULL
    FROM (SELECT t.started_at,
                 MAX(0, COALESCE(t.ended_at,
                                 (SELECT MAX(q.made_at) FROM plays q
                                   WHERE q.user_id = :user AND q.theater_session = t.session),
                                 t.started_at) - t.started_at) * 1000 AS ms,
                 EXISTS (SELECT 1 FROM v WHERE v.theater_session = t.session)
                 AND NOT EXISTS (SELECT 1 FROM v
                                  WHERE v.theater_session = t.session AND NOT v.hid) AS hid
            FROM theater_sessions t
           WHERE t.user_id = :user AND t.started_at >= :start AND t.started_at < :end) w
),
k AS (
  SELECT c.kind, c.hid, c.asset_id, c.started_at
    FROM (SELECT COALESCE(p.kind, 'video') AS kind,
                 p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid,
                 p.asset_id, p.started_at
            FROM plays p
           WHERE p.user_id = :user AND p.started_at >= :start AND p.started_at < :end
             AND COALESCE(p.screen, '') <> 'theater'
          UNION ALL
          SELECT kind, hid, asset_id, started_at FROM s WHERE kind = 'theater') c
   WHERE NOT EXISTS (SELECT 1 FROM plays q
                      WHERE q.user_id = :user
                        AND q.started_at < c.started_at AND q.started_at >= c.started_at - 86400
                        AND MAX(q.made_at, q.started_at + q.duration_ms / 1000)
                            > c.started_at - 1800)
     AND NOT EXISTS (SELECT 1 FROM theater_sessions r
                      WHERE r.user_id = :user
                        AND r.started_at < c.started_at AND r.started_at >= c.started_at - 86400
                        AND COALESCE(r.ended_at, r.started_at) > c.started_at - 1800)
),
f AS (SELECT MIN(started_at) AS first FROM k),
n AS (
  SELECT MIN(q.started_at) AS next
    FROM plays q
   WHERE q.user_id = :user AND q.started_at >= :end AND q.started_at < :end + 86400
     AND NOT EXISTS (SELECT 1 FROM plays r
                      WHERE r.user_id = :user
                        AND r.started_at < q.started_at AND r.started_at >= q.started_at - 86400
                        AND MAX(r.made_at, r.started_at + r.duration_ms / 1000)
                            > q.started_at - 1800)
     AND NOT EXISTS (SELECT 1 FROM theater_sessions r
                      WHERE r.user_id = :user
                        AND r.started_at < q.started_at AND r.started_at >= q.started_at - 86400
                        AND COALESCE(r.ended_at, r.started_at) > q.started_at - 1800)
),
e AS (
  SELECT s.hid, s.ended_at FROM s, f WHERE s.started_at >= f.first
  UNION ALL
  SELECT q.asset_id IN (SELECT value FROM json_each(:hidden)),
         MAX(q.made_at, q.started_at + q.duration_ms / 1000)
    FROM plays q, f, n
   WHERE q.user_id = :user AND f.first IS NOT NULL
     AND q.started_at >= :end AND q.started_at < COALESCE(n.next, :end + 86400)
),
m AS (
  SELECT hid,
         CASE WHEN ended_at < :end
              THEN CAST(strftime('%H', ended_at, 'unixepoch', 'localtime') AS INTEGER) * 60
                   + CAST(strftime('%M', ended_at, 'unixepoch', 'localtime') AS INTEGER)
              ELSE 1440 + (ended_at - :end) / 60 END AS minute
    FROM e
)
SELECT '' AS key, MAX(minute) AS whole,
       MAX(minute) - COALESCE(MAX(CASE WHEN NOT hid THEN minute END), 0) AS hidden
  FROM m HAVING COUNT(*) > 0
"""

# FILES rated and starred, each file once a day however often its rating moved: the sentence these
# feed is "You rated 15 files and starred 7", beside the files starred and the O presses as covers
# (the Opinions block). Counted as every rating and star of anything (people, Sites, Tags), once
# per change, a month of rating people would read as files rated. A file is hidden by the
# vault's verdict, as for every other figure about files.
_RATED = """
SELECT '' AS key, COUNT(*) AS whole, SUM(x.hid) AS hidden
  FROM (SELECT o.subject_id, MAX(o.subject_id IN (SELECT value FROM json_each(:hidden))) AS hid
          FROM opinions o
         WHERE o.user_id = :user AND o.at >= :start AND o.at < :end
           AND o.kind = 'rating' AND o.subject_kind = 'asset' AND o.after IS NOT NULL
         GROUP BY o.subject_id) x
HAVING COUNT(*) > 0
"""

_STARRED = """
SELECT '' AS key, COUNT(*) AS whole, SUM(x.hid) AS hidden
  FROM (SELECT o.subject_id, MAX(o.subject_id IN (SELECT value FROM json_each(:hidden))) AS hid
          FROM opinions o
         WHERE o.user_id = :user AND o.at >= :start AND o.at < :end
           AND o.kind = 'favorite' AND o.subject_kind = 'asset' AND o.after = 1
         GROUP BY o.subject_id) x
HAVING COUNT(*) > 0
"""

# A press is an opinion that raised the count; taking one back lowers it and is not a press.
_O = """
SELECT '' AS key, COUNT(*) AS whole,
       SUM(o.subject_id IN (SELECT value FROM json_each(:hidden))) AS hidden
  FROM opinions o
 WHERE o.user_id = :user AND o.at >= :start AND o.at < :end
   AND o.kind = 'o' AND o.subject_kind = 'asset' AND COALESCE(o.after, 0) > COALESCE(o.before, 0)
HAVING COUNT(*) > 0
"""

_O_FILE = """
SELECT o.subject_id AS key, COUNT(*) AS whole,
       SUM(o.subject_id IN (SELECT value FROM json_each(:hidden))) AS hidden
  FROM opinions o
 WHERE o.user_id = :user AND o.at >= :start AND o.at < :end
   AND o.kind = 'o' AND o.subject_kind = 'asset' AND COALESCE(o.after, 0) > COALESCE(o.before, 0)
 GROUP BY o.subject_id
"""

# The files rated that day, one row each: a period's "You rated 15 files" is the number of
# distinct keys over its days (`router.Book.files`), because `rated` summed over the days would
# count a file rated on three days as three files rated.
_RATED_FILE = """
SELECT o.subject_id AS key, 1 AS whole,
       MAX(o.subject_id IN (SELECT value FROM json_each(:hidden))) AS hidden
  FROM opinions o
 WHERE o.user_id = :user AND o.at >= :start AND o.at < :end
   AND o.kind = 'rating' AND o.subject_kind = 'asset' AND o.after IS NOT NULL
 GROUP BY o.subject_id
"""

# The files starred that day, one row each: the Opinions block's "files you starred" list draws
# them as covers (the Opinions block, without an average). A star switched on, never one taken off.
_STARRED_FILE = """
SELECT o.subject_id AS key, COUNT(*) AS whole,
       SUM(o.subject_id IN (SELECT value FROM json_each(:hidden))) AS hidden
  FROM opinions o
 WHERE o.user_id = :user AND o.at >= :start AND o.at < :end
   AND o.kind = 'favorite' AND o.subject_kind = 'asset' AND COALESCE(o.after, 0) = 1
 GROUP BY o.subject_id
"""

# A decision is a judgement taken on an Organize queue: every row of the record that is not the
# ledger's own. Undone ones are left out: an Undo says the decision was not meant. Nothing about
# a count of decisions names what it was about, so none of it is hidden.
_DECIDED = """
SELECT '' AS key, COUNT(*) AS whole, 0 AS hidden
  FROM workbench_decisions d
 WHERE d.actor_id = :user AND d.actor_kind = 'user'
   AND d.decided_at >= :start AND d.decided_at < :end
   AND d.queue <> 'ledger' AND d.reversed_at IS NULL
HAVING COUNT(*) > 0
"""

_DECIDED_QUEUE = """
SELECT d.queue AS key, COUNT(*) AS whole, 0 AS hidden
  FROM workbench_decisions d
 WHERE d.actor_id = :user AND d.actor_kind = 'user'
   AND d.decided_at >= :start AND d.decided_at < :end
   AND d.queue <> 'ledger' AND d.reversed_at IS NULL
 GROUP BY d.queue
"""

# Faces this User put a name to, counted by FACE rather than by press. The faces board writes one
# receipt per press under the queue `identified` with the verb `decided`, and the faces it named
# are the `track_ids` in its payload. Three acts name faces: a Yes on a "these groups may be her"
# card, and agreeing with a run of matches or of proposals (`FACE_NAMING_ACTS`). Sift's own
# matches are receipts too, and are not this User's, which the actor rules out. The payload is read
# only where it is JSON, so one malformed receipt cannot stop a day from being added up.
_FACES_NAMED = """
SELECT '' AS key,
       SUM(json_array_length(d.payload, '$.track_ids')) AS whole,
       0 AS hidden
  FROM workbench_decisions d
 WHERE d.actor_id = :user AND d.actor_kind = 'user'
   AND d.decided_at >= :start AND d.decided_at < :end
   AND d.queue = 'identified' AND d.verb = 'decided' AND d.reversed_at IS NULL
   AND CASE WHEN json_valid(d.payload) THEN json_extract(d.payload, '$.act') END
       IN ('named-groups', 'agreed-with-matches', 'agreed-with-proposals')
HAVING SUM(json_array_length(d.payload, '$.track_ids')) > 0
"""

# The files filed under a Username or a Site by this User: the ledger's `filed`, counted by file.
_FILES_FILED = """
SELECT '' AS key, COUNT(DISTINCT x.subject_id) AS whole,
       COUNT(DISTINCT CASE WHEN NOT EXISTS (SELECT 1 FROM viewer_assets va
                                             WHERE va.user_id = :user
                                               AND va.asset_id = x.subject_id
                                               AND va.concealed = 0)
                           THEN x.subject_id END) AS hidden
  FROM workbench_decisions d
  JOIN workbench_decision_subjects x ON x.decision_id = d.id AND x.kind = 'asset'
 WHERE d.actor_id = :user AND d.actor_kind = 'user'
   AND d.decided_at >= :start AND d.decided_at < :end
   AND d.verb = 'filed' AND d.reversed_at IS NULL
HAVING COUNT(*) > 0
"""


# Files that arrived, whoever brought them, counted over what this User may see now. The access
# layer's question, because it reads the table that carries the permissions: `arrivals` scopes it
# to the User's stored verdict and answers rows in this table's shape. This module names no
# permission-carrying table, and `tests/test_metrics.py` holds it to that.
async def _files_added(fetch: Fetch, params: Mapping[str, Any]) -> Sequence[Row]:
    return await arrivals.files_added(
        fetch, user_id=str(params["user"]), start=int(params["start"]), end=int(params["end"])
    )


async def _files_added_site(fetch: Fetch, params: Mapping[str, Any]) -> Sequence[Row]:
    return await arrivals.files_added_by_site(
        fetch, user_id=str(params["user"]), start=int(params["start"]), end=int(params["end"])
    )


# Files that left the library, by anyone and however, from what was kept as each one went
# (`schema.file_departures`): counted for a User who could see the file then, and hidden for them as
# it was then. A file deleted before that was kept says nothing about who could see it, and is
# counted for an admin only, who could see every file; none of it hidden.
# Not counted from the deleted lines in History: those would count, for a guest, files nobody had
# ever shared with them, and miss every file the leftovers sweep ended, which writes no such line.
_FILES_REMOVED = """
SELECT '' AS key, COUNT(*) AS whole, COALESCE(SUM(v.concealed), 0) AS hidden
  FROM file_departures f
  LEFT JOIN file_departure_viewers v ON v.user_id = :user AND v.asset_id = f.asset_id
 WHERE f.ended_at >= :start AND f.ended_at < :end
   AND (v.user_id IS NOT NULL
        OR (f.viewers_known = 0
            AND EXISTS (SELECT 1 FROM users u WHERE u.id = :user AND u.role = 'admin')))
HAVING COUNT(*) > 0
"""

# What Sift did (admins only): the time its tasks' runs took, by family, filed under the day a
# run STARTED (`ix_work_runs_recent` answers that; nothing indexes the finish), for runs that ended.
_WORK_MS_FAMILY = """
SELECT r.family AS key, SUM(r.worker_ms) AS whole, 0 AS hidden
  FROM work_runs r
 WHERE r.started_at >= :start AND r.started_at < :end AND r.finished_at IS NOT NULL
 GROUP BY r.family
"""

# By time, on the faces slice's own index (`ix_face_tracks_created`).
#
# IN MILLISECONDS: the faces slice stamps a face found with `now_ms()`, and the day is bound in
# seconds, so the bounds are scaled here. Compared as seconds, every face ever found would sit a
# thousand times past the day's end and every day would say "found 0 faces".
_FACES_FOUND = """
SELECT '' AS key, COUNT(*) AS whole, 0 AS hidden
  FROM face_tracks t
 WHERE t.created_at >= :start * 1000 AND t.created_at < :end * 1000
HAVING COUNT(*) > 0
"""

# `work_runs.files` is the run's files by kind as JSON (`{"video": {"n": 12, "bytes": ...}}`, the
# jobs ledger's `files_total` adds the `n`s). Summed as a column it is TEXT read as a number,
# which is 0: every day would say "fingerprinted 0 files" under hours of runs. A value
# that is not JSON counts nothing rather than stopping the day.
_FINGERPRINTS_MADE = """
SELECT '' AS key, SUM(COALESCE(json_extract(f.value, '$.n'), 0)) AS whole, 0 AS hidden
  FROM work_runs r,
       json_each(CASE WHEN json_valid(r.files) THEN r.files ELSE '{}' END) f
 WHERE r.started_at >= :start AND r.started_at < :end AND r.finished_at IS NOT NULL
   AND r.family = 'fingerprint'
HAVING SUM(COALESCE(json_extract(f.value, '$.n'), 0)) > 0
"""

#: One statement per metric. Each answers `key, whole, hidden` rows for one User's one day.
STATEMENTS: Final[Mapping[str, Counter]] = {
    "viewed_ms": _VIEWED_MS,
    "viewed_ms:kind": _VIEWED_MS_KIND,
    "sittings": _SITTINGS,
    "sittings:kind": _SITTINGS_KIND,
    "files_viewed": _FILES_VIEWED,
    "files_viewed:kind": _FILES_VIEWED_KIND,
    "viewed_ms:person": _VIEWED_MS_PERSON,
    "files_viewed:person": _FILES_VIEWED_PERSON,
    "viewed_ms:site": _VIEWED_MS_SITE,
    "viewed_ms:tag": _VIEWED_MS_TAG,
    "viewed_ms:collection": _VIEWED_MS_COLLECTION,
    "viewed_ms:photo_set": _VIEWED_MS_PHOTO_SET,
    "photo_sets_viewed": _PHOTO_SETS_VIEWED,
    "viewed_ms:song": _VIEWED_MS_SONG,
    "sittings:file": _SITTINGS_FILE,
    "viewed_ms:hour": _VIEWED_MS_HOUR,
    "viewed_ms:weekday": _VIEWED_MS_WEEKDAY,
    "theater_ms:wall": _THEATER_MS_WALL,
    "pickups": _PICKUPS,
    "first_opened:person": _FIRST_OPENED_PERSON,
    "first_opened:kind": _FIRST_OPENED_KIND,
    "earliest_start": _EARLIEST_START,
    "latest_finish": _LATEST_FINISH,
    "rated": _RATED,
    "rated:file": _RATED_FILE,
    "starred": _STARRED,
    "o": _O,
    "o:file": _O_FILE,
    "starred:file": _STARRED_FILE,
    "decided": _DECIDED,
    "decided:queue": _DECIDED_QUEUE,
    "faces_named": _FACES_NAMED,
    "files_filed": _FILES_FILED,
    "files_added": _files_added,
    "files_added:site": _files_added_site,
    "files_removed": _FILES_REMOVED,
    "work_ms:family": _WORK_MS_FAMILY,
    "faces_found": _FACES_FOUND,
    "fingerprints_made": _FINGERPRINTS_MADE,
}


async def rows_of(metric: str, fetch: Fetch, params: Mapping[str, Any]) -> Sequence[Row]:
    """One metric's rows for one User's one day: its statement run with these bindings, or the
    access layer's reader asked with them. The one way a figure is counted."""
    counter = STATEMENTS[metric]
    if isinstance(counter, str):
        return await fetch(counter, params)
    return await counter(fetch, params)


#: The metrics whose rows carry a file's own last vault state: the re-split reads them for a file
#: that no longer has a verdict to ask (see `store.hidden_files`).
PER_FILE: Final = frozenset({"sittings:file", "rated:file", "o:file", "starred:file"})


def split_file_key(key: str) -> tuple[str, str]:
    """A per-file key as `(kind, asset id)`. `sittings:file` writes `<kind>:<asset id>` (an asset
    id is a ULID and carries no colon); `o:file` and `starred:file` write the bare id, whose kind
    is not in the row and reads as empty. `rated:file` writes the bare id too."""
    kind, colon, asset = key.partition(":")
    return (kind, asset) if colon else ("", key)
