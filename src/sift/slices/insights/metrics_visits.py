# SPDX-License-Identifier: AGPL-3.0-or-later
"""The times somebody sat down, and the first and last minute of the day: the statements.

Literals, each with the `k` opening written out; registered in `metrics.STATEMENTS`.
"""

from __future__ import annotations

# A time somebody sat down: a sitting outside Theater, of any length, or the moment a wall began
# to play, with no sitting ending and no wall beginning in the half hour before it. The look back
# reaches into the day before, so the first sitting after midnight is judged as any other. A
# wall's START is somebody pressing it; its cells cycling on is not somebody there, and a wall
# left open with nothing playing is nobody at all: a wall is read as the moment its first cell
# played (`until` is that moment, and nothing for its later cells). `before` is the latest
# `until` of everything that began earlier, read in one pass over the sittings in time order.
#
# Every sitting, not only the views: a sitting-down that began with a file skipped past would
# otherwise have that skip in the half hour before its first view, and count nothing.
PICKUPS = """
WITH k AS (
  SELECT CASE WHEN m.in_theater THEN 'theater' ELSE m.kind END AS kind,
         CASE WHEN m.in_theater THEN NULL ELSE m.asset_id END AS asset_id,
         CASE WHEN m.in_theater THEN COALESCE(g.hid, 0) ELSE m.hid END AS hid, m.started_at
    FROM (SELECT o.*,
                 MAX(o.until) OVER (ORDER BY o.started_at, o.id
                                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS before
            FROM (SELECT p.id, COALESCE(p.kind, 'video') AS kind,
                         COALESCE(p.screen, '') = 'theater' AS in_theater,
                         p.asset_id, p.started_at, p.theater_session,
                         p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid,
                         CASE WHEN COALESCE(p.screen, '') <> 'theater'
                              THEN p.started_at
                                   + MIN(p.duration_ms, CASE WHEN COALESCE(p.kind, 'video') = 'video'
                                                 THEN MAX(900000, 3 * COALESCE(p.length_ms, 0))
                                                 ELSE 900000 END) / 1000
                              WHEN ROW_NUMBER() OVER (PARTITION BY p.theater_session
                                                      ORDER BY p.started_at, p.id) = 1
                              THEN p.started_at END AS until
                    FROM plays p
                   WHERE p.user_id = :user AND p.started_at >= :start - 86400 AND p.started_at < :end) o) m
    LEFT JOIN (SELECT q.theater_session,
                      MIN(COALESCE(q.asset_id IN (SELECT value FROM json_each(:hidden)), 0))
                        AS hid
                 FROM plays q
                WHERE q.user_id = :user AND COALESCE(q.screen, '') = 'theater'
                  AND q.started_at >= :start AND q.started_at < :end
                GROUP BY q.theater_session) g ON g.theater_session = m.theater_session
   WHERE m.started_at >= :start AND m.until IS NOT NULL
     AND COALESCE(m.before, 0) <= m.started_at - 1800
)
SELECT '' AS key, COUNT(*) AS whole, SUM(hid) AS hidden FROM k HAVING COUNT(*) > 0
"""


# The file a time somebody sat down began with. A wall's moment names no file.
FIRST_OPENED_PERSON = """
WITH k AS (
  SELECT CASE WHEN m.in_theater THEN 'theater' ELSE m.kind END AS kind,
         CASE WHEN m.in_theater THEN NULL ELSE m.asset_id END AS asset_id,
         CASE WHEN m.in_theater THEN COALESCE(g.hid, 0) ELSE m.hid END AS hid, m.started_at
    FROM (SELECT o.*,
                 MAX(o.until) OVER (ORDER BY o.started_at, o.id
                                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS before
            FROM (SELECT p.id, COALESCE(p.kind, 'video') AS kind,
                         COALESCE(p.screen, '') = 'theater' AS in_theater,
                         p.asset_id, p.started_at, p.theater_session,
                         p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid,
                         CASE WHEN COALESCE(p.screen, '') <> 'theater'
                              THEN p.started_at
                                   + MIN(p.duration_ms, CASE WHEN COALESCE(p.kind, 'video') = 'video'
                                                 THEN MAX(900000, 3 * COALESCE(p.length_ms, 0))
                                                 ELSE 900000 END) / 1000
                              WHEN ROW_NUMBER() OVER (PARTITION BY p.theater_session
                                                      ORDER BY p.started_at, p.id) = 1
                              THEN p.started_at END AS until
                    FROM plays p
                   WHERE p.user_id = :user AND p.started_at >= :start - 86400 AND p.started_at < :end) o) m
    LEFT JOIN (SELECT q.theater_session,
                      MIN(COALESCE(q.asset_id IN (SELECT value FROM json_each(:hidden)), 0))
                        AS hid
                 FROM plays q
                WHERE q.user_id = :user AND COALESCE(q.screen, '') = 'theater'
                  AND q.started_at >= :start AND q.started_at < :end
                GROUP BY q.theater_session) g ON g.theater_session = m.theater_session
   WHERE m.started_at >= :start AND m.until IS NOT NULL
     AND COALESCE(m.before, 0) <= m.started_at - 1800
)
SELECT x.person_id AS key, COUNT(*) AS whole,
       CASE WHEN EXISTS (SELECT 1 FROM person_user_state h
                          WHERE h.user_id = :user AND h.person_id = x.person_id AND h.hidden = 1)
            THEN COUNT(*) ELSE SUM(k.hid) END AS hidden
  FROM k JOIN asset_people x ON x.asset_id = k.asset_id
 GROUP BY x.person_id
"""


FIRST_OPENED_KIND = """
WITH k AS (
  SELECT CASE WHEN m.in_theater THEN 'theater' ELSE m.kind END AS kind,
         CASE WHEN m.in_theater THEN NULL ELSE m.asset_id END AS asset_id,
         CASE WHEN m.in_theater THEN COALESCE(g.hid, 0) ELSE m.hid END AS hid, m.started_at
    FROM (SELECT o.*,
                 MAX(o.until) OVER (ORDER BY o.started_at, o.id
                                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS before
            FROM (SELECT p.id, COALESCE(p.kind, 'video') AS kind,
                         COALESCE(p.screen, '') = 'theater' AS in_theater,
                         p.asset_id, p.started_at, p.theater_session,
                         p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid,
                         CASE WHEN COALESCE(p.screen, '') <> 'theater'
                              THEN p.started_at
                                   + MIN(p.duration_ms, CASE WHEN COALESCE(p.kind, 'video') = 'video'
                                                 THEN MAX(900000, 3 * COALESCE(p.length_ms, 0))
                                                 ELSE 900000 END) / 1000
                              WHEN ROW_NUMBER() OVER (PARTITION BY p.theater_session
                                                      ORDER BY p.started_at, p.id) = 1
                              THEN p.started_at END AS until
                    FROM plays p
                   WHERE p.user_id = :user AND p.started_at >= :start - 86400 AND p.started_at < :end) o) m
    LEFT JOIN (SELECT q.theater_session,
                      MIN(COALESCE(q.asset_id IN (SELECT value FROM json_each(:hidden)), 0))
                        AS hid
                 FROM plays q
                WHERE q.user_id = :user AND COALESCE(q.screen, '') = 'theater'
                  AND q.started_at >= :start AND q.started_at < :end
                GROUP BY q.theater_session) g ON g.theater_session = m.theater_session
   WHERE m.started_at >= :start AND m.until IS NOT NULL
     AND COALESCE(m.before, 0) <= m.started_at - 1800
)
SELECT kind AS key, COUNT(*) AS whole, SUM(hid) AS hidden FROM k WHERE kind <> 'theater'
 GROUP BY kind
"""


# Minutes of the local day. `whole - hidden` is the minute over what is not hidden (see
# `MINUTES`), so `hidden` is the difference between the two, which is negative for the earliest
# start whenever the first sitting of the day was a hidden one.
#
# BOTH ARE ABOUT A TIME SOMEBODY SAT DOWN (`k`), never about one file opened inside one, and they
# are MOMENTS, not amounts: the evening belongs to the day it started in, whole. An evening past
# midnight goes on opening files after it, and counted per file the next day's "earliest start"
# would be 00:00 on every day of a night owl's month. So the start is the first sitting-down that
# BEGAN this day, and the finish is when the last thing from then on ended, followed past midnight
# until the next time somebody sat down. A day whose only viewing ran on from the night before
# began nothing, and has neither.
EARLIEST_START = """
WITH k AS (
  SELECT CASE WHEN m.in_theater THEN 'theater' ELSE m.kind END AS kind,
         CASE WHEN m.in_theater THEN NULL ELSE m.asset_id END AS asset_id,
         CASE WHEN m.in_theater THEN COALESCE(g.hid, 0) ELSE m.hid END AS hid, m.started_at
    FROM (SELECT o.*,
                 MAX(o.until) OVER (ORDER BY o.started_at, o.id
                                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS before
            FROM (SELECT p.id, COALESCE(p.kind, 'video') AS kind,
                         COALESCE(p.screen, '') = 'theater' AS in_theater,
                         p.asset_id, p.started_at, p.theater_session,
                         p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid,
                         CASE WHEN COALESCE(p.screen, '') <> 'theater'
                              THEN p.started_at
                                   + MIN(p.duration_ms, CASE WHEN COALESCE(p.kind, 'video') = 'video'
                                                 THEN MAX(900000, 3 * COALESCE(p.length_ms, 0))
                                                 ELSE 900000 END) / 1000
                              WHEN ROW_NUMBER() OVER (PARTITION BY p.theater_session
                                                      ORDER BY p.started_at, p.id) = 1
                              THEN p.started_at END AS until
                    FROM plays p
                   WHERE p.user_id = :user AND p.started_at >= :start - 86400 AND p.started_at < :end) o) m
    LEFT JOIN (SELECT q.theater_session,
                      MIN(COALESCE(q.asset_id IN (SELECT value FROM json_each(:hidden)), 0))
                        AS hid
                 FROM plays q
                WHERE q.user_id = :user AND COALESCE(q.screen, '') = 'theater'
                  AND q.started_at >= :start AND q.started_at < :end
                GROUP BY q.theater_session) g ON g.theater_session = m.theater_session
   WHERE m.started_at >= :start AND m.until IS NOT NULL
     AND COALESCE(m.before, 0) <= m.started_at - 1800
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


# Past midnight the minute keeps counting (1440 is midnight at the end of this day). `f` is the
# first sitting-down of this day; `n` the next time somebody sat down after this day ended, by the
# same rule; the evening ends with the last sitting outside Theater from `f` up to `n` (where it
# stopped accruing, `SITTING_CAP_MS`) or the last wall begun in it, never a wall's cells playing on.
# Its files' vault state is asked with the day's own (`DAY_RAN_ON_FILES`).
LATEST_FINISH = """
WITH k AS (
  SELECT CASE WHEN m.in_theater THEN 'theater' ELSE m.kind END AS kind,
         CASE WHEN m.in_theater THEN NULL ELSE m.asset_id END AS asset_id,
         CASE WHEN m.in_theater THEN COALESCE(g.hid, 0) ELSE m.hid END AS hid, m.started_at
    FROM (SELECT o.*,
                 MAX(o.until) OVER (ORDER BY o.started_at, o.id
                                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS before
            FROM (SELECT p.id, COALESCE(p.kind, 'video') AS kind,
                         COALESCE(p.screen, '') = 'theater' AS in_theater,
                         p.asset_id, p.started_at, p.theater_session,
                         p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid,
                         CASE WHEN COALESCE(p.screen, '') <> 'theater'
                              THEN p.started_at
                                   + MIN(p.duration_ms, CASE WHEN COALESCE(p.kind, 'video') = 'video'
                                                 THEN MAX(900000, 3 * COALESCE(p.length_ms, 0))
                                                 ELSE 900000 END) / 1000
                              WHEN ROW_NUMBER() OVER (PARTITION BY p.theater_session
                                                      ORDER BY p.started_at, p.id) = 1
                              THEN p.started_at END AS until
                    FROM plays p
                   WHERE p.user_id = :user AND p.started_at >= :start - 86400 AND p.started_at < :end) o) m
    LEFT JOIN (SELECT q.theater_session,
                      MIN(COALESCE(q.asset_id IN (SELECT value FROM json_each(:hidden)), 0))
                        AS hid
                 FROM plays q
                WHERE q.user_id = :user AND COALESCE(q.screen, '') = 'theater'
                  AND q.started_at >= :start AND q.started_at < :end
                GROUP BY q.theater_session) g ON g.theater_session = m.theater_session
   WHERE m.started_at >= :start AND m.until IS NOT NULL
     AND COALESCE(m.before, 0) <= m.started_at - 1800
),
f AS (SELECT MIN(started_at) AS first FROM k),
n AS (
  SELECT MIN(started_at) AS next
    FROM (SELECT o.*,
                 MAX(o.until) OVER (ORDER BY o.started_at, o.id
                                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS before
            FROM (SELECT p.id, COALESCE(p.kind, 'video') AS kind,
                         COALESCE(p.screen, '') = 'theater' AS in_theater,
                         p.asset_id, p.started_at, p.theater_session,
                         p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid,
                         CASE WHEN COALESCE(p.screen, '') <> 'theater'
                              THEN p.started_at
                                   + MIN(p.duration_ms, CASE WHEN COALESCE(p.kind, 'video') = 'video'
                                                 THEN MAX(900000, 3 * COALESCE(p.length_ms, 0))
                                                 ELSE 900000 END) / 1000
                              WHEN ROW_NUMBER() OVER (PARTITION BY p.theater_session
                                                      ORDER BY p.started_at, p.id) = 1
                              THEN p.started_at END AS until
                    FROM plays p
                   WHERE p.user_id = :user AND p.started_at >= :start AND p.started_at < :end + 86400) o)
   WHERE started_at >= :end AND until IS NOT NULL AND COALESCE(before, 0) <= started_at - 1800
),
e AS (
  SELECT o.hid, o.until AS ended_at
    FROM (SELECT o.*,
                 MAX(o.until) OVER (ORDER BY o.started_at, o.id
                                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS before
            FROM (SELECT p.id, COALESCE(p.kind, 'video') AS kind,
                         COALESCE(p.screen, '') = 'theater' AS in_theater,
                         p.asset_id, p.started_at, p.theater_session,
                         p.asset_id IN (SELECT value FROM json_each(:hidden)) AS hid,
                         CASE WHEN COALESCE(p.screen, '') <> 'theater'
                              THEN p.started_at
                                   + MIN(p.duration_ms, CASE WHEN COALESCE(p.kind, 'video') = 'video'
                                                 THEN MAX(900000, 3 * COALESCE(p.length_ms, 0))
                                                 ELSE 900000 END) / 1000
                              WHEN ROW_NUMBER() OVER (PARTITION BY p.theater_session
                                                      ORDER BY p.started_at, p.id) = 1
                              THEN p.started_at END AS until
                    FROM plays p
                   WHERE p.user_id = :user AND p.started_at >= :start AND p.started_at < :end + 86400) o) o, f, n
   WHERE f.first IS NOT NULL AND o.until IS NOT NULL
     AND o.started_at >= f.first AND o.started_at < COALESCE(n.next, :end + 86400)
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
