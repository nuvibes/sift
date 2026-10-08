# SPDX-License-Identifier: AGPL-3.0-or-later
"""What was rated, decided, filed, arrived, removed and done by Sift on a day: the statements and
the access layer's readers, registered in `metrics.STATEMENTS`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sift.kernel.access import arrivals
from sift.kernel.access.arrivals import Fetch
from sift.kernel.db import Row

# Files rated and starred, each file once a day however often its rating moved; only files, or a
# month of rating people would read as files rated.
RATED = """
SELECT '' AS key, COUNT(*) AS whole, SUM(x.hid) AS hidden
  FROM (SELECT o.subject_id, MAX(o.subject_id IN (SELECT value FROM json_each(:hidden))) AS hid
          FROM opinions o
         WHERE o.user_id = :user AND o.at >= :start AND o.at < :end
           AND o.kind = 'rating' AND o.subject_kind = 'asset' AND o.after IS NOT NULL
         GROUP BY o.subject_id) x
HAVING COUNT(*) > 0
"""


STARRED = """
SELECT '' AS key, COUNT(*) AS whole, SUM(x.hid) AS hidden
  FROM (SELECT o.subject_id, MAX(o.subject_id IN (SELECT value FROM json_each(:hidden))) AS hid
          FROM opinions o
         WHERE o.user_id = :user AND o.at >= :start AND o.at < :end
           AND o.kind = 'favorite' AND o.subject_kind = 'asset' AND o.after = 1
         GROUP BY o.subject_id) x
HAVING COUNT(*) > 0
"""


# A press is an opinion that raised the count; taking one back lowers it and is not a press.
O_PRESSES = """
SELECT '' AS key, COUNT(*) AS whole,
       SUM(o.subject_id IN (SELECT value FROM json_each(:hidden))) AS hidden
  FROM opinions o
 WHERE o.user_id = :user AND o.at >= :start AND o.at < :end
   AND o.kind = 'o' AND o.subject_kind = 'asset' AND COALESCE(o.after, 0) > COALESCE(o.before, 0)
HAVING COUNT(*) > 0
"""


O_FILE = """
SELECT o.subject_id AS key, COUNT(*) AS whole,
       SUM(o.subject_id IN (SELECT value FROM json_each(:hidden))) AS hidden
  FROM opinions o
 WHERE o.user_id = :user AND o.at >= :start AND o.at < :end
   AND o.kind = 'o' AND o.subject_kind = 'asset' AND COALESCE(o.after, 0) > COALESCE(o.before, 0)
 GROUP BY o.subject_id
"""


# One row per file rated, so a period counts distinct files rather than days.
RATED_FILE = """
SELECT o.subject_id AS key, 1 AS whole,
       MAX(o.subject_id IN (SELECT value FROM json_each(:hidden))) AS hidden
  FROM opinions o
 WHERE o.user_id = :user AND o.at >= :start AND o.at < :end
   AND o.kind = 'rating' AND o.subject_kind = 'asset' AND o.after IS NOT NULL
 GROUP BY o.subject_id
"""


# One row per file starred that day; a star switched on, never one taken off.
STARRED_FILE = """
SELECT o.subject_id AS key, COUNT(*) AS whole,
       SUM(o.subject_id IN (SELECT value FROM json_each(:hidden))) AS hidden
  FROM opinions o
 WHERE o.user_id = :user AND o.at >= :start AND o.at < :end
   AND o.kind = 'favorite' AND o.subject_kind = 'asset' AND COALESCE(o.after, 0) = 1
 GROUP BY o.subject_id
"""


# A decision is a judgement on an Organize queue, undone ones left out; a count names nothing, so
# none of it is hidden.
DECIDED = """
SELECT '' AS key, COUNT(*) AS whole, 0 AS hidden
  FROM workbench_decisions d
 WHERE d.actor_id = :user AND d.actor_kind = 'user'
   AND d.decided_at >= :start AND d.decided_at < :end
   AND d.queue <> 'ledger' AND d.reversed_at IS NULL
HAVING COUNT(*) > 0
"""


DECIDED_QUEUE = """
SELECT d.queue AS key, COUNT(*) AS whole, 0 AS hidden
  FROM workbench_decisions d
 WHERE d.actor_id = :user AND d.actor_kind = 'user'
   AND d.decided_at >= :start AND d.decided_at < :end
   AND d.queue <> 'ledger' AND d.reversed_at IS NULL
 GROUP BY d.queue
"""


# Faces this User named, by face: the `track_ids` of the three naming acts' receipts
# (`FACE_NAMING_ACTS`), read only where the payload is JSON so one bad receipt stops nothing.
FACES_NAMED = """
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
FILES_FILED = """
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


# Files that arrived, read through the access layer because the table carries permissions.
async def files_added(fetch: Fetch, params: Mapping[str, Any]) -> Sequence[Row]:
    return await arrivals.files_added(
        fetch, user_id=str(params["user"]), start=int(params["start"]), end=int(params["end"])
    )


async def files_added_site(fetch: Fetch, params: Mapping[str, Any]) -> Sequence[Row]:
    return await arrivals.files_added_by_site(
        fetch, user_id=str(params["user"]), start=int(params["start"]), end=int(params["end"])
    )


# Files that left, from what was kept as each went (`schema.file_departures`): counted for a User who
# could see the file then; one kept with nobody counts for an admin only. Not History's deleted
# lines, which miss the leftovers sweep and would count a guest's unshared files.
FILES_REMOVED = """
SELECT '' AS key, COUNT(*) AS whole, COALESCE(SUM(v.concealed), 0) AS hidden
  FROM file_departures f
  LEFT JOIN file_departure_viewers v ON v.user_id = :user AND v.asset_id = f.asset_id
 WHERE f.ended_at >= :start AND f.ended_at < :end
   AND (v.user_id IS NOT NULL
        OR (f.viewers_known = 0
            AND EXISTS (SELECT 1 FROM users u WHERE u.id = :user AND u.role = 'admin')))
HAVING COUNT(*) > 0
"""


# What Sift did (admins only): runs' time by family, under the day a run started (the index).
WORK_MS_FAMILY = """
SELECT r.family AS key, SUM(r.worker_ms) AS whole, 0 AS hidden
  FROM work_runs r
 WHERE r.started_at >= :start AND r.started_at < :end AND r.finished_at IS NOT NULL
 GROUP BY r.family
"""


# In milliseconds: the faces slice stamps a face with `now_ms()` and the day is bound in seconds.
FACES_FOUND = """
SELECT '' AS key, COUNT(*) AS whole, 0 AS hidden
  FROM face_tracks t
 WHERE t.created_at >= :start * 1000 AND t.created_at < :end * 1000
HAVING COUNT(*) > 0
"""


# `work_runs.files` is JSON by kind; summed as a column it is text read as 0, so the `n`s are
# added from the JSON, and a value that is not JSON counts nothing.
FINGERPRINTS_MADE = """
SELECT '' AS key, SUM(COALESCE(json_extract(f.value, '$.n'), 0)) AS whole, 0 AS hidden
  FROM work_runs r,
       json_each(CASE WHEN json_valid(r.files) THEN r.files ELSE '{}' END) f
 WHERE r.started_at >= :start AND r.started_at < :end AND r.finished_at IS NOT NULL
   AND r.family = 'fingerprint'
HAVING SUM(COALESCE(json_extract(f.value, '$.n'), 0)) > 0
"""
