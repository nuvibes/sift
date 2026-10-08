# SPDX-License-Identifier: AGPL-3.0-or-later
"""Files that arrived in a span of time, counted as one user may see them now.

Readers over `assets` joined to the stored verdict (`viewer_assets`): every file that arrived
between two moments, the same by the Site each file's username belongs to, and the downloads that
finished with the bytes they brought. They live here
rather than beside the feature that draws them because they read the table that carries the
permissions, and the rule of this layer is that nothing outside it does. Each answers rows of the
shape a figure is filed in (a key, the whole count, and how much of it is hidden for this user),
so what a user may not see is decided here, once, and never by the caller.

A file with no username is in the total and under no Site. A Site the user has hidden hides every
file under it, whatever the files' own verdicts say. That is the same reading the vault's own Site
screen gives. Both are bounded by `ix_assets_added_id` on the span and then one primary-key probe
per arrived file, so a day's arrivals cost a day's rows, never the library's.

## A file that has since left

Counted too, so a day's arrivals do not shrink when its files are deleted and the day is added up
again. A departed file has no verdict to ask, so it is read from what was kept as it went
(`file_departures` and who could see it then, `file_departure_viewers`): counted for a user who
could see it, and hidden for them as it was then. A file deleted before that was kept says nothing
about who could see it, and is counted for an admin only, who could see every file. A departed
file's Sites are the ones it was under when it went; a Site the user has hidden now hides it, as
it does a file that is still here. Bounded by `ix_file_departures_arrived` on the span.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Final

from sift.kernel.db import Row, in_clause

#: How a reader runs a statement: what a `Database.fetch_all` bound method is, and what a feature
#: adding figures up hands in so that one counting path serves a stored day and a live one alike.
Fetch = Callable[[str, Mapping[str, Any]], Awaitable[Sequence[Row]]]

#: Files that arrived, whoever brought them, counted over what this user may see now. One row, or
#: none when nothing arrived: a zero is not a figure.
_FILES_ADDED: Final = """
SELECT '' AS key, COUNT(*) AS whole, SUM(x.concealed) AS hidden
  FROM (SELECT va.concealed AS concealed
          FROM assets a
          JOIN viewer_assets va ON va.user_id = :user AND va.asset_id = a.id
         WHERE a.added_at >= :start AND a.added_at < :end
        UNION ALL
        SELECT COALESCE(v.concealed, 0)
          FROM file_departures f
          LEFT JOIN file_departure_viewers v ON v.user_id = :user AND v.asset_id = f.asset_id
         WHERE f.arrived_at >= :start AND f.arrived_at < :end
           AND (v.user_id IS NOT NULL
                OR (f.viewers_known = 0
                    AND EXISTS (SELECT 1 FROM users u WHERE u.id = :user AND u.role = 'admin')))) x
HAVING COUNT(*) > 0
"""

#: The same by Site. A file under two usernames of one Site is one file under it (the DISTINCT);
#: a Site hidden for this user hides every file it reaches.
_FILES_ADDED_BY_SITE: Final = """
SELECT x.site_id AS key, COUNT(*) AS whole,
       CASE WHEN EXISTS (SELECT 1 FROM site_user_state h
                          WHERE h.user_id = :user AND h.site_id = x.site_id AND h.hidden = 1)
            THEN COUNT(*) ELSE SUM(x.concealed) END AS hidden
  FROM (SELECT DISTINCT a.id, u.site_id, va.concealed
          FROM assets a
          JOIN viewer_assets va ON va.user_id = :user AND va.asset_id = a.id
          JOIN asset_usernames au ON au.asset_id = a.id
          JOIN usernames u ON u.id = au.username_id
         WHERE a.added_at >= :start AND a.added_at < :end
        UNION ALL
        SELECT f.asset_id, j.value, COALESCE(v.concealed, 0)
          FROM file_departures f
          JOIN json_each(f.site_ids) j
          LEFT JOIN file_departure_viewers v ON v.user_id = :user AND v.asset_id = f.asset_id
         WHERE f.arrived_at >= :start AND f.arrived_at < :end
           AND (v.user_id IS NOT NULL
                OR (f.viewers_known = 0
                    AND EXISTS (SELECT 1 FROM users w WHERE w.id = :user AND w.role = 'admin')))) x
 GROUP BY x.site_id
"""

#: Downloads that finished in the span, for the user who asked for them (an admin also has the
#: ones from before anybody was recorded as asking). Hidden is a file this user is not shown, or
#: one no longer here to ask about.
_DOWNLOADS_FINISHED: Final = """
SELECT '' AS key, COUNT(*) AS whole, SUM(COALESCE(va.concealed, 1)) AS hidden
  FROM downloads d
  LEFT JOIN viewer_assets va ON va.user_id = :user AND va.asset_id = d.asset_id
 WHERE d.state = 'done' AND d.finished_at >= :start AND d.finished_at < :end
   AND (d.requested_by = :user
        OR (d.requested_by IS NULL
            AND EXISTS (SELECT 1 FROM users u WHERE u.id = :user AND u.role = 'admin')))
HAVING COUNT(*) > 0
"""

#: The same downloads' bytes, over the files still here (a deleted one's size went with it).
_DOWNLOAD_BYTES: Final = """
SELECT '' AS key, SUM(a.size_bytes) AS whole,
       SUM(COALESCE(va.concealed, 1) * a.size_bytes) AS hidden
  FROM downloads d
  JOIN assets a ON a.id = d.asset_id
  LEFT JOIN viewer_assets va ON va.user_id = :user AND va.asset_id = a.id
 WHERE d.state = 'done' AND d.finished_at >= :start AND d.finished_at < :end
   AND (d.requested_by = :user
        OR (d.requested_by IS NULL
            AND EXISTS (SELECT 1 FROM users u WHERE u.id = :user AND u.role = 'admin')))
HAVING SUM(a.size_bytes) > 0
"""

#: The statements, by name, for a test to read: every one must join the stored verdict.
STATEMENTS: Final[Mapping[str, str]] = {
    "files_added": _FILES_ADDED,
    "files_added_by_site": _FILES_ADDED_BY_SITE,
    "downloads_finished": _DOWNLOADS_FINISHED,
    "download_bytes": _DOWNLOAD_BYTES,
}


async def files_added(fetch: Fetch, *, user_id: str, start: int, end: int) -> Sequence[Row]:
    """The files that arrived in `[start, end)` this user may see: one row (`key` empty, `whole`,
    `hidden`), or no rows when none did."""
    return await fetch(_FILES_ADDED, {"user": user_id, "start": start, "end": end})


async def files_added_by_site(fetch: Fetch, *, user_id: str, start: int, end: int) -> Sequence[Row]:
    """`files_added`, one row per Site (`key` is the Site's id), for files under a username."""
    return await fetch(_FILES_ADDED_BY_SITE, {"user": user_id, "start": start, "end": end})


async def downloads_finished(fetch: Fetch, *, user_id: str, start: int, end: int) -> Sequence[Row]:
    """The downloads that finished in `[start, end)` for this user: one row, or none."""
    return await fetch(_DOWNLOADS_FINISHED, {"user": user_id, "start": start, "end": end})


async def download_bytes(fetch: Fetch, *, user_id: str, start: int, end: int) -> Sequence[Row]:
    """The bytes of the files those downloads brought that are still here: one row, or none."""
    return await fetch(_DOWNLOAD_BYTES, {"user": user_id, "start": start, "end": end})


#: What a file is called, by id: its title where somebody typed one, else its name on arrival.
_FILE_NAMES = (
    "SELECT id, COALESCE(NULLIF(title, ''), original_filename, 'a file') AS name"
    " FROM assets WHERE id IN (?*)"
)


#: A read bound by position, for the one statement whose list of ids is expanded in place.
FetchListed = Callable[[str, Sequence[Any]], Awaitable[Sequence[Row]]]


async def file_names(fetch: FetchListed, ids: Sequence[str]) -> Sequence[Row]:
    """The names of these files, for a card that names one: a file no longer here has no row.

    Read here rather than in a slice because `assets` carries permissions: a recap's recipe
    names a file its User viewed, frozen when the recap is made, and what a reader may be told of
    it is decided when the card is drawn.
    """
    if not ids:
        return []
    sql, params = in_clause(_FILE_NAMES, list(ids))
    return await fetch(sql, params)
