# SPDX-License-Identifier: AGPL-3.0-or-later
"""The figures about THINGS viewed: files, People, Sites, Tags, Collections, Photo Sets, songs.

Views outside Theater only (`metrics`, "Theater is one hour per hour it played"). Literals,
each with the `v` opening written out; registered in `metrics.STATEMENTS`.
"""

from __future__ import annotations

FILES_VIEWED = """
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
SELECT '' AS key, COUNT(DISTINCT asset_id) AS whole,
       COUNT(DISTINCT CASE WHEN hid THEN asset_id END) AS hidden
  FROM v WHERE asset_id IS NOT NULL AND began AND NOT in_theater HAVING COUNT(*) > 0
"""


FILES_VIEWED_KIND = """
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
SELECT kind AS key, COUNT(DISTINCT asset_id) AS whole,
       COUNT(DISTINCT CASE WHEN hid THEN asset_id END) AS hidden
  FROM v WHERE asset_id IS NOT NULL AND began AND NOT in_theater GROUP BY kind
"""


VIEWED_MS_PERSON = """
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
SELECT x.person_id AS key, SUM(v.ms) AS whole,
       CASE WHEN EXISTS (SELECT 1 FROM person_user_state h
                          WHERE h.user_id = :user AND h.person_id = x.person_id AND h.hidden = 1)
            THEN SUM(v.ms) ELSE SUM(v.ms * v.hid) END AS hidden
  FROM v JOIN asset_people x ON x.asset_id = v.asset_id
 WHERE NOT v.in_theater
 GROUP BY x.person_id
"""


FILES_VIEWED_PERSON = """
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
SELECT x.person_id AS key, COUNT(DISTINCT v.asset_id) AS whole,
       CASE WHEN EXISTS (SELECT 1 FROM person_user_state h
                          WHERE h.user_id = :user AND h.person_id = x.person_id AND h.hidden = 1)
            THEN COUNT(DISTINCT v.asset_id)
            ELSE COUNT(DISTINCT CASE WHEN v.hid THEN v.asset_id END) END AS hidden
  FROM v JOIN asset_people x ON x.asset_id = v.asset_id
 WHERE v.began AND NOT v.in_theater
 GROUP BY x.person_id
"""


# A file under two Usernames of one Site is one file of that Site: the pairs are made distinct
# before they are joined, or its time would count twice.
VIEWED_MS_SITE = """
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
SELECT x.site_id AS key, SUM(v.ms) AS whole,
       CASE WHEN EXISTS (SELECT 1 FROM site_user_state h
                          WHERE h.user_id = :user AND h.site_id = x.site_id AND h.hidden = 1)
            THEN SUM(v.ms) ELSE SUM(v.ms * v.hid) END AS hidden
  FROM v JOIN (SELECT DISTINCT au.asset_id, u.site_id
                 FROM asset_usernames au JOIN usernames u ON u.id = au.username_id
                WHERE au.asset_id IN (SELECT asset_id FROM v WHERE NOT in_theater)) x
         ON x.asset_id = v.asset_id
 WHERE NOT v.in_theater
 GROUP BY x.site_id
"""


VIEWED_MS_TAG = """
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
SELECT x.tag_id AS key, SUM(v.ms) AS whole,
       CASE WHEN EXISTS (SELECT 1 FROM tag_user_state h
                          WHERE h.user_id = :user AND h.tag_id = x.tag_id AND h.hidden = 1)
            THEN SUM(v.ms) ELSE SUM(v.ms * v.hid) END AS hidden
  FROM v JOIN asset_tags x ON x.asset_id = v.asset_id
 WHERE NOT v.in_theater
 GROUP BY x.tag_id
"""


VIEWED_MS_COLLECTION = """
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
SELECT x.collection_id AS key, SUM(v.ms) AS whole,
       CASE WHEN EXISTS (SELECT 1 FROM collection_user_state h
                          WHERE h.user_id = :user AND h.collection_id = x.collection_id
                            AND h.hidden = 1)
            THEN SUM(v.ms) ELSE SUM(v.ms * v.hid) END AS hidden
  FROM v JOIN collection_items x ON x.asset_id = v.asset_id
 WHERE NOT v.in_theater
 GROUP BY x.collection_id
"""


# A Photo Set has no hidden state of its own for a User: the vault reaches it through its files.
VIEWED_MS_PHOTO_SET = """
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
SELECT x.photo_set_id AS key, SUM(v.ms) AS whole, SUM(v.ms * v.hid) AS hidden
  FROM v JOIN photo_set_items x ON x.asset_id = v.asset_id
 WHERE NOT v.in_theater
 GROUP BY x.photo_set_id
"""


# A song has no hidden state of its own for a User either: it is seen through its files, so the
# part of its time that is hidden is the time spent on its hidden files.
VIEWED_MS_SONG = """
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
SELECT x.song_id AS key, SUM(v.ms) AS whole, SUM(v.ms * v.hid) AS hidden
  FROM v JOIN song_files x ON x.asset_id = v.asset_id
 WHERE NOT v.in_theater
 GROUP BY x.song_id
"""


# A Photo Set counts as hidden when every file of it viewed that day was hidden.
PHOTO_SETS_VIEWED = """
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
SELECT '' AS key, COUNT(*) AS whole, SUM(all_hidden) AS hidden
  FROM (SELECT x.photo_set_id, MIN(v.hid) AS all_hidden
          FROM v JOIN photo_set_items x ON x.asset_id = v.asset_id
         WHERE v.began AND NOT v.in_theater
         GROUP BY x.photo_set_id)
HAVING COUNT(*) > 0
"""


# Keyed `<kind>:<asset id>`, the kind the sitting wrote down: a period's files are the distinct
# keys over its days, and the split by kind is read off the key, so a file deleted since it was
# viewed still counts as the video or picture it was (`split_file_key`).
SITTINGS_FILE = """
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
SELECT kind || ':' || asset_id AS key, COUNT(*) AS whole, SUM(hid) AS hidden
  FROM v WHERE asset_id IS NOT NULL AND began AND NOT in_theater GROUP BY kind, asset_id
"""
