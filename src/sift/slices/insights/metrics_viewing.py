# SPDX-License-Identifier: AGPL-3.0-or-later
"""How long the day was viewed, its sittings, its hours and its Theater walls: the statements.

Literals, each with the openings it needs written out in full (`metrics`, "One statement per
metric"); `test_metrics.py` holds every copy of an opening identical. Registered in
`metrics.STATEMENTS`.
"""

from __future__ import annotations

VIEWED_MS = """
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
),
c AS (
  SELECT COALESCE(q.theater_session, '') AS session, q.started_at,
         MAX(q.started_at * 1000, :start * 1000) AS a,
         MIN(q.started_at * 1000
             + MIN(q.duration_ms, CASE WHEN COALESCE(q.kind, 'video') = 'video'
                                       THEN MAX(900000, 3 * COALESCE(q.length_ms, 0))
                                       ELSE 900000 END),
             :end * 1000) AS b,
         q.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
    FROM plays q
   WHERE q.user_id = :user AND COALESCE(q.screen, '') = 'theater'
     AND q.started_at >= :start - 86400 AND q.started_at < :end
),
i AS (
  SELECT session, MIN(a) AS a, MAX(b) AS b
    FROM (SELECT session, a, b,
                 SUM(fresh) OVER (PARTITION BY session ORDER BY a, b
                                  ROWS UNBOUNDED PRECEDING) AS stretch
            FROM (SELECT session, a, b,
                         COALESCE(a > MAX(b) OVER (PARTITION BY session ORDER BY a, b
                                                   ROWS BETWEEN UNBOUNDED PRECEDING
                                                   AND 1 PRECEDING), 1) AS fresh
                    FROM c WHERE b > a))
   GROUP BY session, stretch
),
w AS (
  SELECT g.session, x.ms, g.hid, g.first >= :start AS began,
         COALESCE(t.arrangement_id, '') AS wall
    FROM (SELECT session, MIN(started_at) AS first, MIN(CASE WHEN b > a THEN hid END) AS hid
            FROM c GROUP BY session) g
    JOIN (SELECT session, SUM(b - a) AS ms FROM i GROUP BY session) x ON x.session = g.session
    LEFT JOIN theater_sessions t ON t.user_id = :user AND t.session = g.session
),
s AS (
  SELECT kind, ms, hid, began, asset_id FROM v WHERE NOT in_theater
  UNION ALL
  SELECT 'theater', ms, hid, began, NULL FROM w
)
SELECT '' AS key, SUM(ms) AS whole, SUM(ms * hid) AS hidden FROM s HAVING COUNT(*) > 0
"""


VIEWED_MS_KIND = """
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
),
c AS (
  SELECT COALESCE(q.theater_session, '') AS session, q.started_at,
         MAX(q.started_at * 1000, :start * 1000) AS a,
         MIN(q.started_at * 1000
             + MIN(q.duration_ms, CASE WHEN COALESCE(q.kind, 'video') = 'video'
                                       THEN MAX(900000, 3 * COALESCE(q.length_ms, 0))
                                       ELSE 900000 END),
             :end * 1000) AS b,
         q.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
    FROM plays q
   WHERE q.user_id = :user AND COALESCE(q.screen, '') = 'theater'
     AND q.started_at >= :start - 86400 AND q.started_at < :end
),
i AS (
  SELECT session, MIN(a) AS a, MAX(b) AS b
    FROM (SELECT session, a, b,
                 SUM(fresh) OVER (PARTITION BY session ORDER BY a, b
                                  ROWS UNBOUNDED PRECEDING) AS stretch
            FROM (SELECT session, a, b,
                         COALESCE(a > MAX(b) OVER (PARTITION BY session ORDER BY a, b
                                                   ROWS BETWEEN UNBOUNDED PRECEDING
                                                   AND 1 PRECEDING), 1) AS fresh
                    FROM c WHERE b > a))
   GROUP BY session, stretch
),
w AS (
  SELECT g.session, x.ms, g.hid, g.first >= :start AS began,
         COALESCE(t.arrangement_id, '') AS wall
    FROM (SELECT session, MIN(started_at) AS first, MIN(CASE WHEN b > a THEN hid END) AS hid
            FROM c GROUP BY session) g
    JOIN (SELECT session, SUM(b - a) AS ms FROM i GROUP BY session) x ON x.session = g.session
    LEFT JOIN theater_sessions t ON t.user_id = :user AND t.session = g.session
),
s AS (
  SELECT kind, ms, hid, began, asset_id FROM v WHERE NOT in_theater
  UNION ALL
  SELECT 'theater', ms, hid, began, NULL FROM w
)
SELECT kind AS key, SUM(ms) AS whole, SUM(ms * hid) AS hidden FROM s GROUP BY kind
"""


SITTINGS = """
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
),
c AS (
  SELECT COALESCE(q.theater_session, '') AS session, q.started_at,
         MAX(q.started_at * 1000, :start * 1000) AS a,
         MIN(q.started_at * 1000
             + MIN(q.duration_ms, CASE WHEN COALESCE(q.kind, 'video') = 'video'
                                       THEN MAX(900000, 3 * COALESCE(q.length_ms, 0))
                                       ELSE 900000 END),
             :end * 1000) AS b,
         q.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
    FROM plays q
   WHERE q.user_id = :user AND COALESCE(q.screen, '') = 'theater'
     AND q.started_at >= :start - 86400 AND q.started_at < :end
),
i AS (
  SELECT session, MIN(a) AS a, MAX(b) AS b
    FROM (SELECT session, a, b,
                 SUM(fresh) OVER (PARTITION BY session ORDER BY a, b
                                  ROWS UNBOUNDED PRECEDING) AS stretch
            FROM (SELECT session, a, b,
                         COALESCE(a > MAX(b) OVER (PARTITION BY session ORDER BY a, b
                                                   ROWS BETWEEN UNBOUNDED PRECEDING
                                                   AND 1 PRECEDING), 1) AS fresh
                    FROM c WHERE b > a))
   GROUP BY session, stretch
),
w AS (
  SELECT g.session, x.ms, g.hid, g.first >= :start AS began,
         COALESCE(t.arrangement_id, '') AS wall
    FROM (SELECT session, MIN(started_at) AS first, MIN(CASE WHEN b > a THEN hid END) AS hid
            FROM c GROUP BY session) g
    JOIN (SELECT session, SUM(b - a) AS ms FROM i GROUP BY session) x ON x.session = g.session
    LEFT JOIN theater_sessions t ON t.user_id = :user AND t.session = g.session
),
s AS (
  SELECT kind, ms, hid, began, asset_id FROM v WHERE NOT in_theater
  UNION ALL
  SELECT 'theater', ms, hid, began, NULL FROM w
)
SELECT '' AS key, COUNT(*) AS whole, SUM(hid) AS hidden FROM s WHERE began HAVING COUNT(*) > 0
"""


SITTINGS_KIND = """
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
),
c AS (
  SELECT COALESCE(q.theater_session, '') AS session, q.started_at,
         MAX(q.started_at * 1000, :start * 1000) AS a,
         MIN(q.started_at * 1000
             + MIN(q.duration_ms, CASE WHEN COALESCE(q.kind, 'video') = 'video'
                                       THEN MAX(900000, 3 * COALESCE(q.length_ms, 0))
                                       ELSE 900000 END),
             :end * 1000) AS b,
         q.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
    FROM plays q
   WHERE q.user_id = :user AND COALESCE(q.screen, '') = 'theater'
     AND q.started_at >= :start - 86400 AND q.started_at < :end
),
i AS (
  SELECT session, MIN(a) AS a, MAX(b) AS b
    FROM (SELECT session, a, b,
                 SUM(fresh) OVER (PARTITION BY session ORDER BY a, b
                                  ROWS UNBOUNDED PRECEDING) AS stretch
            FROM (SELECT session, a, b,
                         COALESCE(a > MAX(b) OVER (PARTITION BY session ORDER BY a, b
                                                   ROWS BETWEEN UNBOUNDED PRECEDING
                                                   AND 1 PRECEDING), 1) AS fresh
                    FROM c WHERE b > a))
   GROUP BY session, stretch
),
w AS (
  SELECT g.session, x.ms, g.hid, g.first >= :start AS began,
         COALESCE(t.arrangement_id, '') AS wall
    FROM (SELECT session, MIN(started_at) AS first, MIN(CASE WHEN b > a THEN hid END) AS hid
            FROM c GROUP BY session) g
    JOIN (SELECT session, SUM(b - a) AS ms FROM i GROUP BY session) x ON x.session = g.session
    LEFT JOIN theater_sessions t ON t.user_id = :user AND t.session = g.session
),
s AS (
  SELECT kind, ms, hid, began, asset_id FROM v WHERE NOT in_theater
  UNION ALL
  SELECT 'theater', ms, hid, began, NULL FROM w
)
SELECT kind AS key, COUNT(*) AS whole, SUM(hid) AS hidden FROM s WHERE began GROUP BY kind
"""


# The hours of this device's clock a sitting's time fell in, each given its own part of it: every
# stretch is already cut to the day, and laid from the top of the clock hour it began in, so an
# evening in Theater from 22:00 to 01:00 is an hour in each of 22 and 23 here and an hour at 00 on
# the next day, and 24 bars never hold more than the day's hours. A wall's time is laid by the
# stretches its cells played, not by its opening. Twenty-five slots, for the day the clocks go
# back; the slot is named by its own local hour, as the clock on the wall did.
VIEWED_MS_HOUR = """
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
),
c AS (
  SELECT COALESCE(q.theater_session, '') AS session, q.started_at,
         MAX(q.started_at * 1000, :start * 1000) AS a,
         MIN(q.started_at * 1000
             + MIN(q.duration_ms, CASE WHEN COALESCE(q.kind, 'video') = 'video'
                                       THEN MAX(900000, 3 * COALESCE(q.length_ms, 0))
                                       ELSE 900000 END),
             :end * 1000) AS b,
         q.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
    FROM plays q
   WHERE q.user_id = :user AND COALESCE(q.screen, '') = 'theater'
     AND q.started_at >= :start - 86400 AND q.started_at < :end
),
i AS (
  SELECT session, MIN(a) AS a, MAX(b) AS b
    FROM (SELECT session, a, b,
                 SUM(fresh) OVER (PARTITION BY session ORDER BY a, b
                                  ROWS UNBOUNDED PRECEDING) AS stretch
            FROM (SELECT session, a, b,
                         COALESCE(a > MAX(b) OVER (PARTITION BY session ORDER BY a, b
                                                   ROWS BETWEEN UNBOUNDED PRECEDING
                                                   AND 1 PRECEDING), 1) AS fresh
                    FROM c WHERE b > a))
   GROUP BY session, stretch
),
w AS (
  SELECT g.session, x.ms, g.hid, g.first >= :start AS began,
         COALESCE(t.arrangement_id, '') AS wall
    FROM (SELECT session, MIN(started_at) AS first, MIN(CASE WHEN b > a THEN hid END) AS hid
            FROM c GROUP BY session) g
    JOIN (SELECT session, SUM(b - a) AS ms FROM i GROUP BY session) x ON x.session = g.session
    LEFT JOIN theater_sessions t ON t.user_id = :user AND t.session = g.session
),
x AS (
  SELECT hid, a, b FROM v WHERE NOT in_theater AND b > a
  UNION ALL
  SELECT w.hid, i.a, i.b FROM i JOIN w ON w.session = i.session
),
h(n) AS (VALUES (0), (1), (2), (3), (4), (5), (6), (7), (8), (9), (10), (11), (12), (13),
                (14), (15), (16), (17), (18), (19), (20), (21), (22), (23), (24)),
y AS (
  SELECT z.hid, z.top + 3600 * h.n AS slot,
         MIN(z.b, (z.top + 3600 * (h.n + 1)) * 1000) - MAX(z.a, (z.top + 3600 * h.n) * 1000) AS ms
    FROM (SELECT hid, a, b,
                 a / 1000
                 - CAST(strftime('%M', a / 1000, 'unixepoch', 'localtime') AS INTEGER) * 60
                 - CAST(strftime('%S', a / 1000, 'unixepoch', 'localtime') AS INTEGER) AS top
            FROM x) z
    JOIN h ON (z.top + 3600 * h.n) * 1000 < z.b
)
SELECT strftime('%H', slot, 'unixepoch', 'localtime') AS key,
       SUM(ms) AS whole, SUM(ms * hid) AS hidden
  FROM y GROUP BY key
"""


# Monday is 0, as Python counts; SQLite's `%w` counts from Sunday. Every amount is already cut
# to the day, so the whole day falls under its own weekday.
VIEWED_MS_WEEKDAY = """
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
),
c AS (
  SELECT COALESCE(q.theater_session, '') AS session, q.started_at,
         MAX(q.started_at * 1000, :start * 1000) AS a,
         MIN(q.started_at * 1000
             + MIN(q.duration_ms, CASE WHEN COALESCE(q.kind, 'video') = 'video'
                                       THEN MAX(900000, 3 * COALESCE(q.length_ms, 0))
                                       ELSE 900000 END),
             :end * 1000) AS b,
         q.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
    FROM plays q
   WHERE q.user_id = :user AND COALESCE(q.screen, '') = 'theater'
     AND q.started_at >= :start - 86400 AND q.started_at < :end
),
i AS (
  SELECT session, MIN(a) AS a, MAX(b) AS b
    FROM (SELECT session, a, b,
                 SUM(fresh) OVER (PARTITION BY session ORDER BY a, b
                                  ROWS UNBOUNDED PRECEDING) AS stretch
            FROM (SELECT session, a, b,
                         COALESCE(a > MAX(b) OVER (PARTITION BY session ORDER BY a, b
                                                   ROWS BETWEEN UNBOUNDED PRECEDING
                                                   AND 1 PRECEDING), 1) AS fresh
                    FROM c WHERE b > a))
   GROUP BY session, stretch
),
w AS (
  SELECT g.session, x.ms, g.hid, g.first >= :start AS began,
         COALESCE(t.arrangement_id, '') AS wall
    FROM (SELECT session, MIN(started_at) AS first, MIN(CASE WHEN b > a THEN hid END) AS hid
            FROM c GROUP BY session) g
    JOIN (SELECT session, SUM(b - a) AS ms FROM i GROUP BY session) x ON x.session = g.session
    LEFT JOIN theater_sessions t ON t.user_id = :user AND t.session = g.session
),
s AS (
  SELECT kind, ms, hid, began, asset_id FROM v WHERE NOT in_theater
  UNION ALL
  SELECT 'theater', ms, hid, began, NULL FROM w
)
SELECT CAST((CAST(strftime('%w', :start, 'unixepoch', 'localtime') AS INTEGER) + 6) % 7
            AS TEXT) AS key,
       SUM(ms) AS whole, SUM(ms * hid) AS hidden
  FROM s HAVING COUNT(*) > 0
"""


THEATER_MS_WALL = """
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
),
c AS (
  SELECT COALESCE(q.theater_session, '') AS session, q.started_at,
         MAX(q.started_at * 1000, :start * 1000) AS a,
         MIN(q.started_at * 1000
             + MIN(q.duration_ms, CASE WHEN COALESCE(q.kind, 'video') = 'video'
                                       THEN MAX(900000, 3 * COALESCE(q.length_ms, 0))
                                       ELSE 900000 END),
             :end * 1000) AS b,
         q.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid
    FROM plays q
   WHERE q.user_id = :user AND COALESCE(q.screen, '') = 'theater'
     AND q.started_at >= :start - 86400 AND q.started_at < :end
),
i AS (
  SELECT session, MIN(a) AS a, MAX(b) AS b
    FROM (SELECT session, a, b,
                 SUM(fresh) OVER (PARTITION BY session ORDER BY a, b
                                  ROWS UNBOUNDED PRECEDING) AS stretch
            FROM (SELECT session, a, b,
                         COALESCE(a > MAX(b) OVER (PARTITION BY session ORDER BY a, b
                                                   ROWS BETWEEN UNBOUNDED PRECEDING
                                                   AND 1 PRECEDING), 1) AS fresh
                    FROM c WHERE b > a))
   GROUP BY session, stretch
),
w AS (
  SELECT g.session, x.ms, g.hid, g.first >= :start AS began,
         COALESCE(t.arrangement_id, '') AS wall
    FROM (SELECT session, MIN(started_at) AS first, MIN(CASE WHEN b > a THEN hid END) AS hid
            FROM c GROUP BY session) g
    JOIN (SELECT session, SUM(b - a) AS ms FROM i GROUP BY session) x ON x.session = g.session
    LEFT JOIN theater_sessions t ON t.user_id = :user AND t.session = g.session
)
SELECT wall AS key, SUM(ms) AS whole, SUM(ms * hid) AS hidden FROM w GROUP BY wall
"""


# The files a wall's cells showed long enough to be views, by the Saved Layout (empty for none):
# kept apart from `files_viewed`, which a wall cycling hundreds of clips would otherwise swamp.
THEATER_FILES = """
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
SELECT COALESCE(t.arrangement_id, '') AS key, COUNT(DISTINCT v.asset_id) AS whole,
       COUNT(DISTINCT CASE WHEN v.hid THEN v.asset_id END) AS hidden
  FROM v LEFT JOIN theater_sessions t ON t.user_id = :user AND t.session = v.theater_session
 WHERE v.in_theater AND v.began AND v.asset_id IS NOT NULL
 GROUP BY COALESCE(t.arrangement_id, '')
"""
