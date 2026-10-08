# SPDX-License-Identifier: AGPL-3.0-or-later
"""The day's first and last file, new favourites and files come back to, the sittings with Sift
and the downloads: the statements and the access layer's readers, registered in
`metrics.STATEMENTS`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sift.kernel.access import arrivals
from sift.kernel.access.arrivals import Fetch
from sift.kernel.db import Row

# The first file viewed outside Theater that day and the last, each as the MOMENT (seconds, the
# tables' own clock) its sitting began or stopped accruing, so a sitting at 00:00 is never a zero
# read as hidden. At most two rows each: the first (or last) of all, and, when that file is
# hidden, the first (or last) that is not, so a locked reader still has one to draw. A hidden
# file's row is wholly hidden.
FIRST_FILE = """
WITH v AS (
  SELECT *, MAX(0, b - a) AS ms
    FROM (SELECT p.id, p.asset_id, COALESCE(p.kind, 'video') AS kind,
                 COALESCE(p.screen, '') = 'theater' AS in_theater, p.theater_session,
                 p.started_at, p.started_at >= :start AS began,
                 MAX(p.started_at * 1000, :start * 1000) AS a,
                 MIN(p.started_at * 1000
                     + MIN(p.duration_ms, CASE WHEN COALESCE(p.kind, 'video') = 'video'
                                               THEN MAX(900000, 3 * COALESCE(p.length_ms, 0))
                                               ELSE 900000 END),
                     :end * 1000) AS b,
                 p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
            FROM plays p
           WHERE p.id IN (SELECT value FROM json_each(:views)) AND p.user_id = :user)
)
SELECT asset_id AS key, started_at AS whole, CASE WHEN hid THEN started_at ELSE 0 END AS hidden
  FROM (SELECT asset_id, started_at, hid,
               ROW_NUMBER() OVER (ORDER BY started_at, id) AS n,
               ROW_NUMBER() OVER (PARTITION BY hid ORDER BY started_at, id) AS m
          FROM v WHERE began AND NOT in_theater AND asset_id IS NOT NULL)
 WHERE n = 1 OR (m = 1 AND NOT hid)
"""


LAST_FILE = """
WITH v AS (
  SELECT *, MAX(0, b - a) AS ms
    FROM (SELECT p.id, p.asset_id, COALESCE(p.kind, 'video') AS kind,
                 COALESCE(p.screen, '') = 'theater' AS in_theater, p.theater_session,
                 p.started_at, p.started_at >= :start AS began,
                 MAX(p.started_at * 1000, :start * 1000) AS a,
                 MIN(p.started_at * 1000
                     + MIN(p.duration_ms, CASE WHEN COALESCE(p.kind, 'video') = 'video'
                                               THEN MAX(900000, 3 * COALESCE(p.length_ms, 0))
                                               ELSE 900000 END),
                     :end * 1000) AS b,
                 p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
            FROM plays p
           WHERE p.id IN (SELECT value FROM json_each(:views)) AND p.user_id = :user)
)
SELECT asset_id AS key, ended_at AS whole, CASE WHEN hid THEN ended_at ELSE 0 END AS hidden
  FROM (SELECT asset_id, hid,
               (SELECT p.started_at
                       + MIN(p.duration_ms, CASE WHEN COALESCE(p.kind, 'video') = 'video'
                                                 THEN MAX(900000, 3 * COALESCE(p.length_ms, 0))
                                                 ELSE 900000 END) / 1000
                  FROM plays p WHERE p.id = v.id) AS ended_at,
               ROW_NUMBER() OVER (ORDER BY started_at DESC, id DESC) AS n,
               ROW_NUMBER() OVER (PARTITION BY hid ORDER BY started_at DESC, id DESC) AS m
          FROM v WHERE began AND NOT in_theater AND asset_id IS NOT NULL)
 WHERE n = 1 OR (m = 1 AND NOT hid)
"""


# A file never sat with before this day (outside Theater) and viewed three times or more in it:
# the value is its views that day.
NEW_FAVOURITES_FILE = """
WITH v AS (
  SELECT *, MAX(0, b - a) AS ms
    FROM (SELECT p.id, p.asset_id, COALESCE(p.kind, 'video') AS kind,
                 COALESCE(p.screen, '') = 'theater' AS in_theater, p.theater_session,
                 p.started_at, p.started_at >= :start AS began,
                 MAX(p.started_at * 1000, :start * 1000) AS a,
                 MIN(p.started_at * 1000
                     + MIN(p.duration_ms, CASE WHEN COALESCE(p.kind, 'video') = 'video'
                                               THEN MAX(900000, 3 * COALESCE(p.length_ms, 0))
                                               ELSE 900000 END),
                     :end * 1000) AS b,
                 p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
            FROM plays p
           WHERE p.id IN (SELECT value FROM json_each(:views)) AND p.user_id = :user)
)
SELECT asset_id AS key, COUNT(*) AS whole, SUM(hid) AS hidden
  FROM v
 WHERE began AND NOT in_theater AND asset_id IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM plays q
                    WHERE q.asset_id = v.asset_id AND q.user_id = :user
                      AND q.started_at < :start AND COALESCE(q.screen, '') <> 'theater')
 GROUP BY asset_id
HAVING COUNT(*) >= 3
"""


# A file viewed this day whose previous sitting (outside Theater) was 180 days or more before:
# the value is the days between.
REDISCOVERED_FILE = """
WITH v AS (
  SELECT *, MAX(0, b - a) AS ms
    FROM (SELECT p.id, p.asset_id, COALESCE(p.kind, 'video') AS kind,
                 COALESCE(p.screen, '') = 'theater' AS in_theater, p.theater_session,
                 p.started_at, p.started_at >= :start AS began,
                 MAX(p.started_at * 1000, :start * 1000) AS a,
                 MIN(p.started_at * 1000
                     + MIN(p.duration_ms, CASE WHEN COALESCE(p.kind, 'video') = 'video'
                                               THEN MAX(900000, 3 * COALESCE(p.length_ms, 0))
                                               ELSE 900000 END),
                     :end * 1000) AS b,
                 p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
            FROM plays p
           WHERE p.id IN (SELECT value FROM json_each(:views)) AND p.user_id = :user)
)
SELECT asset_id AS key, (first - before) / 86400 AS whole,
       CASE WHEN hid THEN (first - before) / 86400 ELSE 0 END AS hidden
  FROM (SELECT v.asset_id, MIN(v.started_at) AS first, MAX(v.hid) AS hid,
               (SELECT MAX(q.started_at) FROM plays q
                 WHERE q.asset_id = v.asset_id AND q.user_id = :user
                   AND q.started_at < :start AND COALESCE(q.screen, '') <> 'theater') AS before
          FROM v WHERE v.began AND NOT v.in_theater AND v.asset_id IS NOT NULL
         GROUP BY v.asset_id)
 WHERE (first - before) / 86400 >= 180
"""


# A sitting with Sift on one device (`app_sessions`), its time inside this day: a session past
# midnight is filed on both days, each with its own part. Nothing in it names a thing, so none of
# it is hidden.
SESSION_MS_SESSION = """
SELECT x.id AS key,
       MIN(x.last_at_ms, :end * 1000) - MAX(x.started_at_ms, :start * 1000) AS whole, 0 AS hidden
  FROM app_sessions x
 WHERE x.user_id = :user AND x.started_at_ms < :end * 1000 AND x.last_at_ms > :start * 1000
"""


# The pages opened in each session this day; a page about something hidden is the hidden part.
SESSION_PAGES_SESSION = """
SELECT g.app_session_id AS key, COUNT(*) AS whole, SUM(g.hidden) AS hidden
  FROM page_visits g
 WHERE g.user_id = :user AND g.opened_at_ms >= :start * 1000 AND g.opened_at_ms < :end * 1000
 GROUP BY g.app_session_id
"""


# Downloads that finished this day, and their files' bytes: the access layer's, for the reason the
# arrivals are (a downloaded file's size is on the permission-carrying row).
async def downloads(fetch: Fetch, params: Mapping[str, Any]) -> Sequence[Row]:
    return await arrivals.downloads_finished(
        fetch, user_id=str(params["user"]), start=int(params["start"]), end=int(params["end"])
    )


async def download_bytes(fetch: Fetch, params: Mapping[str, Any]) -> Sequence[Row]:
    return await arrivals.download_bytes(
        fetch, user_id=str(params["user"]), start=int(params["start"]), end=int(params["end"])
    )
