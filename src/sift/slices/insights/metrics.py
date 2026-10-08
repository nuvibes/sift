# SPDX-License-Identifier: AGPL-3.0-or-later
"""What is counted about a User's day: a closed list, and the one statement that counts each.

`METRICS` is the list and `STATEMENTS` holds one literal statement for each, held equal by a test:
two spellings of one figure would be two figures. A value is a count or milliseconds, except the
minutes of the day (`earliest_start`, `latest_finish`), the moments of `first_file` and `last_file`
(seconds) and the days of `rediscovered:file`.

Every statement is one literal, never assembled from pieces (the codebase's rule for SQL), so the
shared openings are written out again where needed and `test_metrics.py` holds every copy identical.
Each takes `:user`, `:start` and `:end` (the day as seconds on this device's clock), `:views` (the
`plays` ids the player's own rule counted as views, decided once in Python, among the sittings that
began this day or ran into it) and `:hidden` (the file ids hidden for this User), and answers
`key, whole, hidden` rows.

Time falls on the day it was spent: an amount is cut at midnight, a count belongs to the day the
sitting began, a moment to the day the evening began. A sitting stops accruing at `SITTING_CAP_MS`
for a picture or a GIF and at `VIDEO_LOOPS` times a video's length, because the player has no rule
of its own for somebody having walked away. A Theater wall is the union of its cells' plays, one hour
per hour, nothing when nothing played; its cells are left out of every figure about a thing (files,
People, Sites, Tags, Collections, Photo Sets, songs), and what they showed is `theater_files`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Final

from sift.kernel.access.arrivals import Fetch
from sift.kernel.db import Row
from sift.slices.insights import metrics_record as record
from sift.slices.insights import metrics_sessions as sessions
from sift.slices.insights import metrics_things as things
from sift.slices.insights import metrics_viewing as viewing
from sift.slices.insights import metrics_visits as visits

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
        "theater_files",
        "first_file",
        "last_file",
        "new_favourites:file",
        "rediscovered:file",
        "session_ms:session",
        "session_pages:session",
        "downloads",
        "download_bytes",
    }
)

#: The statements' version: a recap keeps the one it was made at, so a correction can find it.
METRICS_VERSION: Final = 7

#: What Sift did rather than the User (admins only), added up for every User since a role can change.
ADMIN_ONLY: Final = frozenset({"work_ms:family", "faces_found", "fingerprints_made"})

#: The metrics whose value is a minute of the day: locked, `whole - hidden` is 0 when every sitting
#: was hidden, so a reader asks `sittings` first.
MINUTES: Final = frozenset({"earliest_start", "latest_finish"})

#: The faces board's naming acts, written out again in `metrics_record.FACES_NAMED` (a literal) and
#: held equal by `test_metrics.py`.
FACE_NAMING_ACTS: Final = frozenset(
    {"named-groups", "agreed-with-matches", "agreed-with-proposals"}
)

#: How long after a sitting ended the next one has to start to be a new time somebody sat down.
PICKUP_GAP_SECONDS: Final = 30 * 60

#: The most one sitting with a picture or a GIF counts, and the floor of a video's own limit.
SITTING_CAP_MS: Final = 15 * 60 * 1000

#: How many times its own length one sitting with a video counts at most.
VIDEO_LOOPS: Final = 3

#: How many views in a day make a file never sat with before "a new favourite".
NEW_FAVOURITE_VIEWS: Final = 3

#: How long a file has to have gone without a sitting to be "rediscovered" when it is viewed again.
REDISCOVERED_AFTER_DAYS: Final = 180


# --- the inputs every statement shares --------------------------------------------------------

#: The day's sittings with what the view rule judges them by, as each wrote it down when it began
#: (`plays.kind`, `plays.length_ms`), never the file itself; the day before's that ran into this day
#: are among them.
DAY_PLAYS: Final = """
SELECT p.id AS id,
       p.asset_id AS asset_id,
       p.duration_ms AS watched_ms,
       COALESCE(p.kind, 'video') AS media_type,
       p.length_ms AS length_ms
  FROM plays p
 WHERE p.user_id = :user AND p.started_at >= :start - 86400 AND p.started_at < :end
   AND MAX(p.made_at, p.started_at + p.duration_ms / 1000) >= :start
"""

#: The files the day's opinions were about, so their vault state is asked once with the viewed ones.
DAY_OPINION_FILES: Final = """
SELECT DISTINCT o.subject_id AS asset_id
  FROM opinions o
 WHERE o.user_id = :user AND o.at >= :start AND o.at < :end AND o.subject_kind = 'asset'
"""

#: The files opened the next day, since the latest finish follows the evening past midnight; asking
#: a state it does not use costs nothing and leaks nothing.
DAY_RAN_ON_FILES: Final = """
SELECT DISTINCT p.asset_id AS asset_id
  FROM plays p
 WHERE p.user_id = :user AND p.started_at >= :end AND p.started_at < :end + 86400
   AND p.asset_id IS NOT NULL
"""

#: Whether there is anything to count for this User on this day, so a day of nothing costs one read;
#: the faces found arrive with a run, and the files that arrived are the access layer's question.
ANYTHING: Final = """
SELECT EXISTS (SELECT 1 FROM plays p
                WHERE p.user_id = :user AND p.started_at >= :start - 86400 AND p.started_at < :end
                  AND MAX(p.made_at, p.started_at + p.duration_ms / 1000) >= :start)
    OR EXISTS (SELECT 1 FROM theater_sessions s
                WHERE s.user_id = :user AND s.started_at >= :start AND s.started_at < :end)
    OR EXISTS (SELECT 1 FROM opinions o
                WHERE o.user_id = :user AND o.at >= :start AND o.at < :end)
    OR EXISTS (SELECT 1 FROM workbench_decisions d
                WHERE d.decided_at >= :start AND d.decided_at < :end)
    OR EXISTS (SELECT 1 FROM work_runs r WHERE r.started_at >= :start AND r.started_at < :end)
    OR EXISTS (SELECT 1 FROM file_departures f WHERE f.ended_at >= :start AND f.ended_at < :end)
    OR EXISTS (SELECT 1 FROM app_sessions x
                WHERE x.user_id = :user AND x.started_at_ms < :end * 1000
                  AND x.last_at_ms > :start * 1000)
    OR EXISTS (SELECT 1 FROM downloads d
                WHERE d.finished_at >= :start AND d.finished_at < :end AND d.state = 'done')
    AS anything
"""


# --- the openings every family's statements share -----------------------------------------
#
# Seven openings recur, each copy held identical by `test_metrics.py`: v the day's views as the
# stretch each accrued inside the day (capped, cut at both midnights, `began` for counts); c every
# cell's play reaching into the day; i each wall's played stretches (the union of its cells); w each
# wall's time, hidden state, first day and Saved Layout; s the sittings for amounts; k the pickups.


#: One statement per metric. Each answers `key, whole, hidden` rows for one User's one day.
STATEMENTS: Final[Mapping[str, Counter]] = {
    "viewed_ms": viewing.VIEWED_MS,
    "viewed_ms:kind": viewing.VIEWED_MS_KIND,
    "sittings": viewing.SITTINGS,
    "sittings:kind": viewing.SITTINGS_KIND,
    "files_viewed": things.FILES_VIEWED,
    "files_viewed:kind": things.FILES_VIEWED_KIND,
    "viewed_ms:person": things.VIEWED_MS_PERSON,
    "files_viewed:person": things.FILES_VIEWED_PERSON,
    "viewed_ms:site": things.VIEWED_MS_SITE,
    "viewed_ms:tag": things.VIEWED_MS_TAG,
    "viewed_ms:collection": things.VIEWED_MS_COLLECTION,
    "viewed_ms:photo_set": things.VIEWED_MS_PHOTO_SET,
    "photo_sets_viewed": things.PHOTO_SETS_VIEWED,
    "viewed_ms:song": things.VIEWED_MS_SONG,
    "sittings:file": things.SITTINGS_FILE,
    "viewed_ms:hour": viewing.VIEWED_MS_HOUR,
    "viewed_ms:weekday": viewing.VIEWED_MS_WEEKDAY,
    "theater_ms:wall": viewing.THEATER_MS_WALL,
    "pickups": visits.PICKUPS,
    "first_opened:person": visits.FIRST_OPENED_PERSON,
    "first_opened:kind": visits.FIRST_OPENED_KIND,
    "earliest_start": visits.EARLIEST_START,
    "latest_finish": visits.LATEST_FINISH,
    "rated": record.RATED,
    "rated:file": record.RATED_FILE,
    "starred": record.STARRED,
    "o": record.O_PRESSES,
    "o:file": record.O_FILE,
    "starred:file": record.STARRED_FILE,
    "decided": record.DECIDED,
    "decided:queue": record.DECIDED_QUEUE,
    "faces_named": record.FACES_NAMED,
    "files_filed": record.FILES_FILED,
    "files_added": record.files_added,
    "files_added:site": record.files_added_site,
    "files_removed": record.FILES_REMOVED,
    "work_ms:family": record.WORK_MS_FAMILY,
    "faces_found": record.FACES_FOUND,
    "fingerprints_made": record.FINGERPRINTS_MADE,
    "theater_files": viewing.THEATER_FILES,
    "first_file": sessions.FIRST_FILE,
    "last_file": sessions.LAST_FILE,
    "new_favourites:file": sessions.NEW_FAVOURITES_FILE,
    "rediscovered:file": sessions.REDISCOVERED_FILE,
    "session_ms:session": sessions.SESSION_MS_SESSION,
    "session_pages:session": sessions.SESSION_PAGES_SESSION,
    "downloads": sessions.downloads,
    "download_bytes": sessions.download_bytes,
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
PER_FILE: Final = frozenset(
    {
        "sittings:file",
        "rated:file",
        "o:file",
        "starred:file",
        "first_file",
        "last_file",
        "new_favourites:file",
        "rediscovered:file",
    }
)


def split_file_key(key: str) -> tuple[str, str]:
    """A per-file key as `(kind, asset id)`. `sittings:file` writes `<kind>:<asset id>` (an asset
    id is a ULID and carries no colon); `o:file` and `starred:file` write the bare id, whose kind
    is not in the row and reads as empty. `rated:file` writes the bare id too."""
    kind, colon, asset = key.partition(":")
    return (kind, asset) if colon else ("", key)
