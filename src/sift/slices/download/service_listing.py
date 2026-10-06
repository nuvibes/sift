# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading the ledger for the Downloads screen: the list, its counts, the rail and one row."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, get_args

from sift.kernel.access import (
    is_refusal,
)
from sift.kernel.access.sites import site_names_for_keys_on
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce_now
from sift.kernel.jobs import Workspaces
from sift.kernel.sorting import sort_key
from sift.slices.download.service_base import (
    _GET_DOWNLOAD,
    _JOB_STATE,
    DownloadBase,
)
from sift.slices.download.service_sites import (
    _by_site,
    _creator_scope_of,
    _display_status,
    _shown_url,
    _site_named,
    _site_of,
    site_name_of,
)
from sift.slices.download.service_views import (
    SiteCount,
)
from sift.slices.download.sources.registry import site_key


@dataclass(frozen=True, slots=True)
class DownloadView:
    """One ledger row as a screen sees it, with the job's state folded into a display status."""

    id: str
    status: str
    dest_folder_id: str | None
    site: str | None
    username: str | None
    asset_id: str | None
    error: str | None
    created_at: int
    #: Short and stable, for recognising a failure without matching its wording.
    error_code: str | None = None
    #: How much the sentence is worth: 3 was written about this site, 1 is a status code's phrase.
    error_tier: int | None = None
    #: The way out this download took, as a person reads it: Direct, or a tunnel's name.
    via: str | None = None
    #: The server a tunnelled download went out through, as it stood when the route was taken.
    via_address: str | None = None
    #: The folder this download's job RESOLVED (`record_folder`); None before it got that far.
    folder_id: str | None = None
    #: What the site is called, worked out from the address so it is known from the paste on.
    site_name: str | None = None
    #: What this download's creator picture is filed under; absent where there is none to ask for.
    creator_scope: str | None = None
    #: The supported site's stable key, from the ADDRESS rather than the Site it was filed under.
    site_key: str | None = None
    #: The address, exactly as it was pasted.
    url: str | None = None
    #: The address with a signed link's expiry and signature taken off, for reading only: it is NOT
    #: a working link. Absent when the address is already the short one.
    shown_url: str | None = None
    #: When it stopped, for a row that has: what a settled row is timed by.
    finished_at: int | None = None
    #: The Site and the person it was filed under, as ids, so both names on a row can be opened.
    site_id: str | None = None
    person_id: str | None = None
    #: What the download produced, as recorded: what a row says once the file itself is gone.
    remembered_filename: str | None = None
    #: The job that runs this row, so Activity and the queue can name the same piece of work.
    job_id: str | None = None
    #: Where in the line this one is, counting from 1; absent for a row not waiting its turn.
    position: int | None = None
    #: How many files the link offered and how many were left out for good (`mark_done`).
    files_offered: int | None = None
    files_left_out: int | None = None
    reads_refused: str | None = None


@dataclass(frozen=True, slots=True)
class RailFacts:
    """What the Downloads row on the rail says, read from the download rows (`_RAIL_COUNTS`)."""

    downloading: int
    waiting_for_cookies: int
    landed_unseen: int
    failed_unseen: int


@dataclass(frozen=True, slots=True)
class DownloadPage:
    """A page of the ledger, how many rows there are, and how the queue as a whole is doing."""

    downloads: list[DownloadView]
    total: int
    running: int = 0
    queued: int = 0
    #: Every shown state and how many rows wear it, over the whole queue. See `_STATE_COUNTS`.
    by_state: dict[str, int] = field(default_factory=dict)
    #: How many rows the narrowing keeps: what a pager divides into pages. `total` is the whole
    #: queue, which is what the header counts; the two differ exactly when something is narrowed.
    matched: int = 0
    #: The same counts as `by_state`, inside the chosen Site: what each chip would show if it were
    #: pressed. Equal to `by_state` when no Site is chosen.
    counts: dict[str, int] = field(default_factory=dict)
    #: Every Site the queue holds rows from, inside the lit chip, with how many. The Site menu.
    sites: list[SiteCount] = field(default_factory=list)


#: Which part of the queue is asked for: the state tabs' ids, so a tab, the route and the statement
#: cannot drift. `needs` is blocked or failed: the rows waiting on a person.
DownloadShow = Literal["all", "active", "needs", "done", "failed"]


DOWNLOAD_SHOWS: tuple[str, ...] = get_args(DownloadShow)


#: How the list may be ordered. The first two page in SQL; the rest are worked out in Python over
#: the narrowed rows, because what they order by is not a column the ledger's statement can see.
#: See `_MATCHING`.
DownloadSort = Literal["newest", "oldest", "name_az", "name_za", "largest", "smallest", "site"]


DOWNLOAD_SORTS: tuple[str, ...] = get_args(DownloadSort)


@dataclass(frozen=True, slots=True)
class DownloadNarrowing:
    """What a screen has narrowed the queue to, and the order it wants it in."""

    show: str = "all"
    #: Sites' NAMES, as the filter panel's Site column offers them. Several is either of them
    #: (the same "or" every wall's facet reads a repeated value as), and none is every Site. A
    #: name with a leading minus is "not this Site", the spelling every wall reads (`is_refusal`).
    sites: tuple[str, ...] = ()
    search: str = ""
    sort: str = "newest"


@dataclass(frozen=True, slots=True)
class FileFacts:
    """What the file a download produced is called and how big it is, as the access layer read it."""

    name: str | None
    size: int | None


#: Reads the file facts for a set of asset ids, scoped to whoever is looking. Handed in by the
#: route, because the file's name and size live on a permission-carrying table this slice may not
#: read (`sift-no-asset-sql-outside-kernel`): the ordering by name or size asks the one reader
#: that decides who may see what, and a file that reader withholds sorts as if it had no file.
FilesOf = Callable[[Sequence[str]], Awaitable[Mapping[str, FileFacts]]]


# The ledger row with the job that runs it folded in. A download has at most one download job, found
# by the id in the job's payload, so a left join names the job or leaves the columns null when there
# is none yet. The window count rides on the same statement so the total cannot drift from the page.
# One row per download, and the join is why that has to be said out loud: "download anyway"
# enqueues a second job against the same download id, so a join onto every job would fan out:
# `total` (`COUNT(*) OVER ()`) would over-count, and the screen, which keys the list by id, would
# abandon the whole list on a repeated key.
#
# Newest first, and newest means most recently finished rather than most recently queued:
# downloads overtake each other constantly: a small file queued after a large one finishes long
# before it. A row that has not finished has nothing to sort by yet and falls back to when it was
# queued, which is the only thing true about it.
#
# The job is the one the row names: `downloads.job_id`, written whenever a job is queued for the
# row, so a retried download names its newest job and the join can only ever produce one row per
# ledger row, without reading every job's payload.
#
# The site and the person are the ids the row was filed under, and they are what
# make the two names on a row into ways in. Kept by id rather than joined by the names the row
# recorded, because a name is somebody's to change: joined by name, renaming a Site would stop
# every row from it leading there. The Site's name is read live through its id for the same reason; the
# words the address gave stay as the row's fallback. A row that has not succeeded has neither id,
# and its names are plain text, since there is nothing to open yet.
#
# What is not read here is the file itself. Its name and size live on a permission-carrying table,
# and every read of one goes through the access layer so that one copy of the rule about who may see
# what decides it, so the row carries the id and the caller asks for the rest. A row whose file has
# since been deleted comes back without either, which is the truth about it: the ledger keeps the
# history, the file is gone.
#
# Where it is in the line, for a row that is waiting. One window function over the download jobs
# that are claimable, ranked in the order the queue claims them, joined onto the page, so a row
# that is not waiting has no number at all rather than a number that means nothing.
#
# Ranked over the jobs and not over the ledger rows, which is the whole reason this is a join
# rather than a count of older rows. Priority lives on the job: moving a download to the front
# writes the queue's lowest priority onto its job and touches the ledger row not at all, so a
# position worked out from `downloads.created_at` would show a promoted download still
# fourteenth while it runs next.
#
# The order is the claim's (priority, then when it was made, then its id), and the condition is
# the claim's too, down to the wait on `run_after`: a job that is not due yet is not in the line,
# and counting it would put somebody behind a row that is not going to be taken before them. It is
# the queue's own rule, written here a second time because SQL cannot call across to it, and what
# holds the two together is that both are stated in those terms rather than approximated.
#
# ## Narrowed on the server, because the list is paged
#
# The chips, the Site, the search and the order all narrow this statement rather than the page a
# screen happens to hold. A chip that filtered fifty rows on the client would answer "which of
# these fifty are done", and the question it is labeled with is "which downloads are done".
#
# Every narrowing is a bound value, never text joined into the statement (the repository's SQL rule
# refuses a joined statement in a slice, constants or not). A narrowing that is off is the value
# that makes its test true (`all`, an empty search, no hosts), so one statement serves every
# combination and the planner sees one shape.
#
# `shown` is the fold, `host` is the address's authority (lower-cased, cut at the first `/`, `?` or
# `#` after the scheme) and `words` is what the search box reads. The Site a host belongs to is
# worked out in Python from that host (`_site_named`), because the catalog is Python: the statement
# is handed the HOSTS that make up the chosen Site, as a JSON array, and never a Site's name. A
# Site refused ("not this", a leading minus) is handed over the same way, as the hosts to leave out.
#
# The same CTE opens all five statements below and the same WHERE closes three of them. The SQL
# rule means they are written out rather than joined, and `test_the_narrowing_is_one_rule` holds
# the copies to `_SHOWN_ROWS` and `_NARROWED` byte for byte.
_SHOWN_ROWS = """
shown AS (
  SELECT d.id, d.url, d.asset_id, d.filename, d.finished_at, d.created_at,
         CASE
           WHEN j.state = 'blocked' THEN 'blocked'
           WHEN j.state = 'paused' THEN 'paused'
           WHEN j.state = 'failed'
            AND d.state NOT IN ('done','duplicate','failed','skipped','canceled','quarantined')
           THEN 'failed'
           ELSE d.state
         END AS shown,
         lower(CASE WHEN instr(d.url, '://') = 0 THEN ''
           ELSE substr(substr(d.url, instr(d.url, '://') + 3), 1,
                instr(replace(replace(substr(d.url, instr(d.url, '://') + 3), '?', '/'), '#', '/')
                      || '/', '/') - 1)
         END) AS host,
         lower(COALESCE(d.filename, '') || ' ' || COALESCE(d.url, '') || ' '
               || COALESCE(d.site, '') || ' ' || COALESCE(d.username, '') || ' '
               || COALESCE(d.error, '') || ' ' || COALESCE(d.via, '')) AS words
    FROM downloads d
    LEFT JOIN jobs j ON j.id = d.job_id
   WHERE d.hidden_at IS NULL
)"""

_NARROWED = """
 WHERE (:show = 'all'
        OR (:show = 'active' AND s.shown IN ('queued','running','blocked','paused'))
        OR (:show = 'needs' AND s.shown IN ('blocked','failed'))
        OR (:show IN ('done','failed') AND s.shown = :show))
   AND (:hosts IS NULL OR s.host IN (SELECT value FROM json_each(:hosts)))
   AND (:refused IS NULL OR s.host NOT IN (SELECT value FROM json_each(:refused)))
   AND (:q = '' OR instr(s.words, :q) > 0)"""

_LIST_DOWNLOADS = """
WITH waiting AS (
  SELECT j.id AS job_id, ROW_NUMBER() OVER (ORDER BY j.priority, j.id) AS place
    FROM jobs j
   WHERE j.type = 'download' AND j.state = 'queued'
     AND (j.run_after IS NULL OR j.run_after <= :now)
),
shown AS (
  SELECT d.id, d.url, d.asset_id, d.filename, d.finished_at, d.created_at,
         CASE
           WHEN j.state = 'blocked' THEN 'blocked'
           WHEN j.state = 'paused' THEN 'paused'
           WHEN j.state = 'failed'
            AND d.state NOT IN ('done','duplicate','failed','skipped','canceled','quarantined')
           THEN 'failed'
           ELSE d.state
         END AS shown,
         lower(CASE WHEN instr(d.url, '://') = 0 THEN ''
           ELSE substr(substr(d.url, instr(d.url, '://') + 3), 1,
                instr(replace(replace(substr(d.url, instr(d.url, '://') + 3), '?', '/'), '#', '/')
                      || '/', '/') - 1)
         END) AS host,
         lower(COALESCE(d.filename, '') || ' ' || COALESCE(d.url, '') || ' '
               || COALESCE(d.site, '') || ' ' || COALESCE(d.username, '') || ' '
               || COALESCE(d.error, '') || ' ' || COALESCE(d.via, '')) AS words
    FROM downloads d
    LEFT JOIN jobs j ON j.id = d.job_id
   WHERE d.hidden_at IS NULL
)
SELECT d.id, d.state, d.dest_folder_id,
       COALESCE((SELECT sn.name FROM sites sn WHERE sn.id = d.site_id), d.site) AS site,
       d.username,
       d.asset_id, d.error, d.created_at, d.finished_at, d.error_code, d.error_tier, d.via,
       d.via_address, d.folder_id,
       d.url, d.filename AS remembered_filename, d.files_offered, d.files_left_out, d.reads_refused,
       d.site_id AS site_id,
       d.person_id AS person_id,
       d.job_id,
       j.state AS job_state,
       w.place AS position
  FROM shown s
  JOIN downloads d ON d.id = s.id
  LEFT JOIN jobs j ON j.id = d.job_id
  LEFT JOIN waiting w ON w.job_id = d.job_id
 WHERE (:show = 'all'
        OR (:show = 'active' AND s.shown IN ('queued','running','blocked','paused'))
        OR (:show = 'needs' AND s.shown IN ('blocked','failed'))
        OR (:show IN ('done','failed') AND s.shown = :show))
   AND (:hosts IS NULL OR s.host IN (SELECT value FROM json_each(:hosts)))
   AND (:refused IS NULL OR s.host NOT IN (SELECT value FROM json_each(:refused)))
   AND (:q = '' OR instr(s.words, :q) > 0)
   AND (:ids IS NULL OR s.id IN (SELECT value FROM json_each(:ids)))
 -- ordered by the clock: a download is placed by when it finished, a moment only the clock
 -- records; created_at stands in until it has
 ORDER BY CASE WHEN :sort = 'oldest' THEN COALESCE(s.finished_at, s.created_at) END ASC,
          CASE WHEN :sort = 'oldest' THEN s.id END ASC,
          COALESCE(s.finished_at, s.created_at) DESC, s.id DESC
 LIMIT :limit OFFSET :offset
"""

#: Every row the narrowing keeps, with only what an order worked out in Python needs: the name,
#: the Site and the size are not columns this statement can sort by (the size and the file's own
#: name are on a permission-carrying table, the Site is the catalog's answer about a host). Read
#: only for those orders; the two orders by time page in SQL above.
_MATCHING = """
WITH shown AS (
  SELECT d.id, d.url, d.asset_id, d.filename, d.finished_at, d.created_at,
         CASE
           WHEN j.state = 'blocked' THEN 'blocked'
           WHEN j.state = 'paused' THEN 'paused'
           WHEN j.state = 'failed'
            AND d.state NOT IN ('done','duplicate','failed','skipped','canceled','quarantined')
           THEN 'failed'
           ELSE d.state
         END AS shown,
         lower(CASE WHEN instr(d.url, '://') = 0 THEN ''
           ELSE substr(substr(d.url, instr(d.url, '://') + 3), 1,
                instr(replace(replace(substr(d.url, instr(d.url, '://') + 3), '?', '/'), '#', '/')
                      || '/', '/') - 1)
         END) AS host,
         lower(COALESCE(d.filename, '') || ' ' || COALESCE(d.url, '') || ' '
               || COALESCE(d.site, '') || ' ' || COALESCE(d.username, '') || ' '
               || COALESCE(d.error, '') || ' ' || COALESCE(d.via, '')) AS words
    FROM downloads d
    LEFT JOIN jobs j ON j.id = d.job_id
   WHERE d.hidden_at IS NULL
)
SELECT s.id, s.url, s.asset_id, s.filename, s.host,
       COALESCE(s.finished_at, s.created_at) AS settled
  FROM shown s
 WHERE (:show = 'all'
        OR (:show = 'active' AND s.shown IN ('queued','running','blocked','paused'))
        OR (:show = 'needs' AND s.shown IN ('blocked','failed'))
        OR (:show IN ('done','failed') AND s.shown = :show))
   AND (:hosts IS NULL OR s.host IN (SELECT value FROM json_each(:hosts)))
   AND (:refused IS NULL OR s.host NOT IN (SELECT value FROM json_each(:refused)))
   AND (:q = '' OR instr(s.words, :q) > 0)
"""

#: How many rows the narrowing keeps: what a pager divides into pages. The same `hidden_at` test
#: as the page, and it has to be: a count that included rows the list leaves out would offer a last
#: page with nothing on it. There is no way to ask for the hidden ones: a row put away is put
#: away, and a parameter that brought them back would be a second list nobody has a screen for.
_COUNT_MATCHING = """
WITH shown AS (
  SELECT d.id, d.url, d.asset_id, d.filename, d.finished_at, d.created_at,
         CASE
           WHEN j.state = 'blocked' THEN 'blocked'
           WHEN j.state = 'paused' THEN 'paused'
           WHEN j.state = 'failed'
            AND d.state NOT IN ('done','duplicate','failed','skipped','canceled','quarantined')
           THEN 'failed'
           ELSE d.state
         END AS shown,
         lower(CASE WHEN instr(d.url, '://') = 0 THEN ''
           ELSE substr(substr(d.url, instr(d.url, '://') + 3), 1,
                instr(replace(replace(substr(d.url, instr(d.url, '://') + 3), '?', '/'), '#', '/')
                      || '/', '/') - 1)
         END) AS host,
         lower(COALESCE(d.filename, '') || ' ' || COALESCE(d.url, '') || ' '
               || COALESCE(d.site, '') || ' ' || COALESCE(d.username, '') || ' '
               || COALESCE(d.error, '') || ' ' || COALESCE(d.via, '')) AS words
    FROM downloads d
    LEFT JOIN jobs j ON j.id = d.job_id
   WHERE d.hidden_at IS NULL
)
SELECT COUNT(*) AS total
  FROM shown s
 WHERE (:show = 'all'
        OR (:show = 'active' AND s.shown IN ('queued','running','blocked','paused'))
        OR (:show = 'needs' AND s.shown IN ('blocked','failed'))
        OR (:show IN ('done','failed') AND s.shown = :show))
   AND (:hosts IS NULL OR s.host IN (SELECT value FROM json_each(:hosts)))
   AND (:refused IS NULL OR s.host NOT IN (SELECT value FROM json_each(:refused)))
   AND (:q = '' OR instr(s.words, :q) > 0)
"""

#: How many rows wear each state a SCREEN shows, over the whole queue, or over one Site's part
#: of it, and never over a page.
#:
#: The chips above the list say "Done 182" and "Failed 4", and a chip that counted the fifty rows
#: on screen would answer a question nobody asked. Asked twice when a Site is chosen: once with no
#: hosts, for the strip
#: and the header, which are about the whole queue, and once with the Site's, for the chips, which
#: count what pressing them would show. The search narrows neither: a chip answers "how many
#: failures are there", not "how many failures match what is typed".
_STATE_COUNTS = """
WITH shown AS (
  SELECT d.id, d.url, d.asset_id, d.filename, d.finished_at, d.created_at,
         CASE
           WHEN j.state = 'blocked' THEN 'blocked'
           WHEN j.state = 'paused' THEN 'paused'
           WHEN j.state = 'failed'
            AND d.state NOT IN ('done','duplicate','failed','skipped','canceled','quarantined')
           THEN 'failed'
           ELSE d.state
         END AS shown,
         lower(CASE WHEN instr(d.url, '://') = 0 THEN ''
           ELSE substr(substr(d.url, instr(d.url, '://') + 3), 1,
                instr(replace(replace(substr(d.url, instr(d.url, '://') + 3), '?', '/'), '#', '/')
                      || '/', '/') - 1)
         END) AS host,
         lower(COALESCE(d.filename, '') || ' ' || COALESCE(d.url, '') || ' '
               || COALESCE(d.site, '') || ' ' || COALESCE(d.username, '') || ' '
               || COALESCE(d.error, '') || ' ' || COALESCE(d.via, '')) AS words
    FROM downloads d
    LEFT JOIN jobs j ON j.id = d.job_id
   WHERE d.hidden_at IS NULL
)
SELECT s.shown, COUNT(*) AS how_many
  FROM shown s
 WHERE (:hosts IS NULL OR s.host IN (SELECT value FROM json_each(:hosts)))
   AND (:refused IS NULL OR s.host NOT IN (SELECT value FROM json_each(:refused)))
 GROUP BY s.shown
"""

#: What the Downloads row on the rail shows: how many are fetching or waiting their turn, how many
#: wait for cookies, and how many ended unseen, landed or failed. Folded the way every row is (the
#: `shown` CTE above), so the rail and the screen cannot come to call one row two things; `seen_at`
#: is read off the row itself, which the fold does not carry. Put-away rows are left out by the
#: fold, as they are everywhere: putting a row away is looking at it.
#:
#: Read from the rows rather than from a page of the work queue. That page is the newest fifty jobs
#: of every kind, and a download can scroll off it (behind a few seconds of background work)
#: before it ends, so its ending would never be read and the dot would never light.
_RAIL_COUNTS = """
WITH shown AS (
  SELECT d.id, d.url, d.asset_id, d.filename, d.finished_at, d.created_at,
         CASE
           WHEN j.state = 'blocked' THEN 'blocked'
           WHEN j.state = 'paused' THEN 'paused'
           WHEN j.state = 'failed'
            AND d.state NOT IN ('done','duplicate','failed','skipped','canceled','quarantined')
           THEN 'failed'
           ELSE d.state
         END AS shown,
         lower(CASE WHEN instr(d.url, '://') = 0 THEN ''
           ELSE substr(substr(d.url, instr(d.url, '://') + 3), 1,
                instr(replace(replace(substr(d.url, instr(d.url, '://') + 3), '?', '/'), '#', '/')
                      || '/', '/') - 1)
         END) AS host,
         lower(COALESCE(d.filename, '') || ' ' || COALESCE(d.url, '') || ' '
               || COALESCE(d.site, '') || ' ' || COALESCE(d.username, '') || ' '
               || COALESCE(d.error, '') || ' ' || COALESCE(d.via, '')) AS words
    FROM downloads d
    LEFT JOIN jobs j ON j.id = d.job_id
   WHERE d.hidden_at IS NULL
)
SELECT COALESCE(SUM(s.shown = 'running'), 0) AS running,
       COALESCE(SUM(s.shown = 'queued'), 0) AS queued,
       COALESCE(SUM(s.shown = 'blocked'), 0) AS blocked,
       COALESCE(SUM(s.shown IN ('done','duplicate','skipped','quarantined')
                    AND d.seen_at IS NULL), 0) AS landed,
       COALESCE(SUM(s.shown = 'failed' AND d.seen_at IS NULL), 0) AS failed
  FROM shown s
  JOIN downloads d ON d.id = s.id
"""

#: Somebody has looked at how the downloads ended. Every row, ended or not: one still in flight
#: loses nothing by being marked, because its ending clears the mark (the finishing statements).
_MARK_SEEN = "UPDATE downloads SET seen_at = ? WHERE seen_at IS NULL"

#: Every host the queue holds rows from and how many, inside the chip that is lit: what the Site
#: menu is built from, once the hosts are folded into the Sites they belong to. Inside the chip and
#: not inside the search, for the reason the chips are not inside the Site: each narrowing counts
#: what the OTHER one leaves, which is what pressing it would show.
_HOST_COUNTS = """
WITH shown AS (
  SELECT d.id, d.url, d.asset_id, d.filename, d.finished_at, d.created_at,
         CASE
           WHEN j.state = 'blocked' THEN 'blocked'
           WHEN j.state = 'paused' THEN 'paused'
           WHEN j.state = 'failed'
            AND d.state NOT IN ('done','duplicate','failed','skipped','canceled','quarantined')
           THEN 'failed'
           ELSE d.state
         END AS shown,
         lower(CASE WHEN instr(d.url, '://') = 0 THEN ''
           ELSE substr(substr(d.url, instr(d.url, '://') + 3), 1,
                instr(replace(replace(substr(d.url, instr(d.url, '://') + 3), '?', '/'), '#', '/')
                      || '/', '/') - 1)
         END) AS host,
         lower(COALESCE(d.filename, '') || ' ' || COALESCE(d.url, '') || ' '
               || COALESCE(d.site, '') || ' ' || COALESCE(d.username, '') || ' '
               || COALESCE(d.error, '') || ' ' || COALESCE(d.via, '')) AS words
    FROM downloads d
    LEFT JOIN jobs j ON j.id = d.job_id
   WHERE d.hidden_at IS NULL
)
SELECT s.host, COUNT(*) AS how_many
  FROM shown s
 WHERE (:show = 'all'
        OR (:show = 'active' AND s.shown IN ('queued','running','blocked','paused'))
        OR (:show = 'needs' AND s.shown IN ('blocked','failed'))
        OR (:show IN ('done','failed') AND s.shown = :show))
 GROUP BY s.host
"""

#: The address one ledger row holds, for seeding the file it produced. Asked by download id and
#: not by asset id: one paste can yield several files, and each of them came from this address.
_URL_OF_DOWNLOAD = "SELECT url FROM downloads WHERE id = ?"


class DownloadListing(DownloadBase):
    """The pages of the list, what narrows them, and the numbers drawn beside them."""

    # --- Reading the ledger --------------------------------------------------------------

    async def kept_on_disk(
        self,
        views: Sequence[DownloadView],
        workspaces: Workspaces | None,
        *,
        measured: Collection[str],
    ) -> dict[str, int]:
        """What each PAUSED download has kept, read off its job's workspace. Download id to bytes.

        Only for a paused row the progress registry has no figure for, which is a paused row after
        a restart: the registry is memory and went with the process, while the bytes it described
        stayed in the job's workspace, which the queue keeps for a paused job and the boot sweep
        leaves alone. So the disk is the durable answer, and the registry's figure (where there
        is one) is only a faster report of the same fact, which is why a row it already measures
        is not read twice. A row whose workspace holds nothing, or has none, is simply absent: "0
        bytes kept" would be a figure for a download that never started.

        The total is not known here (the site said it to the fetch, and nothing wrote it down), so
        the row reads "8.1 MB kept" rather than "8.1 of 226 MB kept" until Resume asks again.
        """
        wanted = {
            view.id: view.job_id
            for view in views
            if view.status == "paused" and view.job_id and view.id not in measured
        }
        if not wanted or workspaces is None:
            return {}

        def measure() -> dict[str, int]:
            kept: dict[str, int] = {}
            for download_id, job_id in wanted.items():
                size = workspaces.size_of(job_id)
                if size:
                    kept[download_id] = size
            return kept

        return await asyncio.to_thread(measure)

    async def list_downloads(
        self,
        *,
        limit: int,
        offset: int = 0,
        narrowing: DownloadNarrowing | None = None,
        files_of: FilesOf | None = None,
    ) -> DownloadPage:
        """A page of the ledger, narrowed and ordered, each row shown with its job's state folded in.

        Newest first unless asked otherwise. The two orders by time page in SQL; name, Site and size
        read the narrowed rows' ids, order them here and read the page by those ids. The ledger
        itself bounds that, and it is only paid for when one of the three is asked for.
        """
        wanted = narrowing or DownloadNarrowing()
        show = wanted.show if wanted.show in DOWNLOAD_SHOWS else "all"
        sort = wanted.sort if wanted.sort in DOWNLOAD_SORTS else "newest"
        search = wanted.search.strip().lower()
        by_host = await self._host_counts(show)
        hosts, refused = await self._site_filters(wanted, show, by_host)
        narrowed = {"show": show, "hosts": hosts, "refused": refused, "q": search}
        rows, matched = await self._page_rows(narrowed, sort, limit, offset, files_of)
        downloads = await self._views(rows)
        by_state = {
            str(row["shown"]): int(row["how_many"])
            for row in await self._db.fetch_all(_STATE_COUNTS, {"hosts": None, "refused": None})
        }
        counts = (
            by_state
            if hosts is None and refused is None
            else {
                str(row["shown"]): int(row["how_many"])
                for row in await self._db.fetch_all(
                    _STATE_COUNTS, {"hosts": hosts, "refused": refused}
                )
            }
        )
        total = sum(by_state.values())
        if matched is None and show == "all" and hosts is None and refused is None and not search:
            matched = total
        elif matched is None:
            counted = await self._db.fetch_one(_COUNT_MATCHING, narrowed)
            matched = int(counted["total"]) if counted is not None else 0
        # The strip's two figures are the shown states' counts, folded the way each row is: a job
        # waiting for cookies has a ledger row that still says running. One fold, one place.
        return DownloadPage(
            downloads=downloads,
            total=total,
            running=by_state.get("running", 0),
            queued=by_state.get("queued", 0),
            by_state=by_state,
            matched=matched,
            counts=counts,
            sites=_by_site(by_host),
        )

    async def _site_filters(
        self, wanted: DownloadNarrowing, show: str, by_host: Mapping[str, int]
    ) -> tuple[str | None, str | None]:
        """The hosts of the Sites chosen and of the Sites refused, as JSON lists, or None."""
        hosts: str | None = None
        refused: str | None = None
        picked = [one.strip() for one in wanted.sites if one.strip()]
        chosen = {one.casefold() for one in picked if not is_refusal(one)}
        unwanted = {one[1:].casefold() for one in picked if is_refusal(one)}
        if chosen or unwanted:
            # The Sites' hosts across EVERY state, not only inside the lit tab: the tabs count
            # the chosen Sites under each state.
            every = by_host if show == "all" else await self._host_counts("all")
            if chosen:
                hosts = json.dumps(
                    sorted(host for host in every if (_site_named(host) or "").casefold() in chosen)
                )
            if unwanted:
                refused = json.dumps(
                    sorted(
                        host for host in every if (_site_named(host) or "").casefold() in unwanted
                    )
                )
        return hosts, refused

    async def _page_rows(
        self,
        narrowed: Mapping[str, object],
        sort: str,
        limit: int,
        offset: int,
        files_of: FilesOf | None,
    ) -> tuple[list[Any], int | None]:
        """One page of rows in the order asked for, and how many matched where that is known."""
        if sort in ("newest", "oldest"):
            rows = await self._db.fetch_all(
                _LIST_DOWNLOADS,
                {
                    **narrowed,
                    "now": int(time.time()),
                    "ids": None,
                    "sort": sort,
                    "limit": limit,
                    "offset": offset,
                },
            )
            return list(rows), None
        ids = await self._ordered_ids(narrowed, sort, files_of)
        page_ids = ids[offset : offset + limit]
        if not page_ids:
            return [], len(ids)
        found = await self._db.fetch_all(
            _LIST_DOWNLOADS,
            {
                **narrowed,
                "now": int(time.time()),
                "ids": json.dumps(page_ids),
                "sort": "newest",
                "limit": len(page_ids),
                "offset": 0,
            },
        )
        # Read in the statement's own order, put back into the one worked out above.
        where = {one: at for at, one in enumerate(page_ids)}
        return sorted(found, key=lambda row: where[str(row["id"])]), len(ids)

    async def _views(self, rows: Sequence[Any]) -> list[DownloadView]:
        """The rows as the screen draws them, each Site named as it is called HERE, read live."""
        keys = sorted({key for row in rows if (key := site_key(str(row["url"]))) is not None})
        async with self._db.read() as connection:
            called = await site_names_for_keys_on(connection, keys)
        return [
            DownloadView(
                id=row["id"],
                status=_display_status(row["state"], row["job_state"]),
                dest_folder_id=row["dest_folder_id"],
                site=row["site"],
                username=row["username"],
                asset_id=row["asset_id"],
                error=row["error"],
                created_at=row["created_at"],
                error_code=row["error_code"],
                error_tier=row["error_tier"],
                remembered_filename=row["remembered_filename"],
                via=row["via"],
                via_address=row["via_address"],
                folder_id=row["folder_id"],
                site_key=_site_of(row["url"]),
                site_name=called.get(site_key(str(row["url"])) or "") or site_name_of(row["url"]),
                creator_scope=_creator_scope_of(row["url"], row["username"]),
                url=row["url"],
                shown_url=_shown_url(row["url"]),
                finished_at=row["finished_at"],
                site_id=row["site_id"],
                person_id=row["person_id"],
                job_id=row["job_id"],
                position=None if row["position"] is None else int(row["position"]),
                files_offered=row["files_offered"],
                files_left_out=row["files_left_out"],
                reads_refused=row["reads_refused"],
            )
            for row in rows
        ]

    async def rail(self, *, paused: bool) -> RailFacts:
        """What the Downloads row on the rail says. One read of the rows; see `_RAIL_COUNTS`."""
        row = await self._db.fetch_one(_RAIL_COUNTS, ())
        running, queued, blocked, landed, failed = (
            (0, 0, 0, 0, 0)
            if row is None
            else (
                int(row["running"]),
                int(row["queued"]),
                int(row["blocked"]),
                int(row["landed"]),
                int(row["failed"]),
            )
        )
        return RailFacts(
            downloading=running + (0 if paused else queued),
            waiting_for_cookies=blocked,
            landed_unseen=landed,
            failed_unseen=failed,
        )

    async def mark_seen(self) -> int:
        """Somebody has looked at how the downloads ended. How many rows that changed.

        Announced only when it changed something, so opening the Downloads screen with nothing new
        does not make every window re-read the rail for nothing.
        """
        async with self._db.write() as connection:
            before = connection.total_changes
            await connection.execute(_MARK_SEEN, (int(time.time()),))
            count = connection.total_changes - before
        # After the block, which is the commit: the same order `_say` keeps.
        if count:
            announce_now(EVERY_ADMIN, About.DOWNLOADS)
        return count

    async def site_counts(self, show: str = "all") -> list[SiteCount]:
        """Every Site the queue holds rows from inside the tab `show` names, and how many.

        The Site column of the filter panel on the Downloads screen. The same fold of hosts into
        Sites the list's own `sites` is, from the same statement: one answer to "which Sites, and
        how many", read by the page and by the panel, rather than two that could come to count one
        queue differently.
        """
        return _by_site(await self._host_counts(show if show in DOWNLOAD_SHOWS else "all"))

    async def _host_counts(self, show: str) -> dict[str, int]:
        """How many rows each host holds inside the tab `show` names."""
        return {
            str(row["host"]): int(row["how_many"])
            for row in await self._db.fetch_all(_HOST_COUNTS, {"show": show})
        }

    async def _ordered_ids(
        self, narrowed: Mapping[str, Any], sort: str, files_of: FilesOf | None
    ) -> list[str]:
        """Every narrowed row's id, in the order asked for, for the orders SQL cannot see.

        By name, either way round: the file's own name where the access layer gives one, else
        what the ledger remembered, else the address: the same order of answers the row is drawn
        under. By Site: the Site's name, newest first inside it. By size, either way round: a row
        with no file (still waiting, failed, or its file since deleted) after every row that has
        one. Newest first breaks every tie.
        """
        rows = await self._db.fetch_all(_MATCHING, narrowed)
        files: Mapping[str, FileFacts] = {}
        if sort in ("name_az", "name_za", "largest", "smallest") and files_of is not None:
            files = await files_of([str(row["asset_id"]) for row in rows if row["asset_id"]])

        def facts(row: Any) -> FileFacts | None:
            return files.get(str(row["asset_id"])) if row["asset_id"] else None

        def recency(row: Any) -> tuple[int, str]:
            # Newest first as the tie-break under every order, the way the list reads at rest.
            return (-int(row["settled"] or 0), str(row["id"]))

        if sort in ("name_az", "name_za"):

            def by_name(row: Any) -> str:
                file = facts(row)
                name = (file.name if file else None) or row["filename"]
                name = name or _shown_url(row["url"]) or row["url"] or ""
                return sort_key(str(name))

            # Two passes rather than one key, so Z-A turns the NAMES round and nothing else: a
            # stable sort keeps the first pass's newest-first among rows of one name either way.
            rows = sorted(sorted(rows, key=recency), key=by_name, reverse=sort == "name_za")
        elif sort == "site":
            rows = sorted(
                rows,
                key=lambda row: (sort_key(_site_named(str(row["host"])) or "~"), recency(row)),
            )
        else:
            # Smallest turns the SIZES round and nothing else: a row with no file is last under
            # both, because "no file" is not a very small file.
            direction = 1 if sort == "smallest" else -1

            def by_size(row: Any) -> tuple[int, int, tuple[int, str]]:
                file = facts(row)
                size = file.size if file and file.size is not None else None
                return (0 if size is not None else 1, direction * (size or 0), recency(row))

            rows = sorted(rows, key=by_size)
        return [str(row["id"]) for row in rows]

    async def get(self, download_id: str) -> DownloadView | None:
        """One ledger row by id, or None. Reads the job alongside it for the display status."""
        row = await self._db.fetch_one(_GET_DOWNLOAD, (download_id,))
        if row is None:
            return None
        job = (
            await self._db.fetch_one(_JOB_STATE, (row["job_id"],))
            if row["job_id"] is not None
            else None
        )
        return DownloadView(
            id=row["id"],
            status=_display_status(row["state"], job["state"] if job else None),
            dest_folder_id=row["dest_folder_id"],
            site=row["site"],
            username=row["username"],
            asset_id=row["asset_id"],
            error=row["error"],
            created_at=row["created_at"],
            error_code=row["error_code"],
            error_tier=row["error_tier"],
            via=row["via"],
            via_address=row["via_address"],
            folder_id=row["folder_id"],
            site_key=_site_of(row["url"]),
            site_name=site_name_of(row["url"]),
            creator_scope=_creator_scope_of(row["url"], row["username"]),
            url=row["url"],
            finished_at=row["finished_at"],
            site_id=row["site_id"],
            person_id=row["person_id"],
            job_id=row["job_id"],
            files_offered=row["files_offered"],
            files_left_out=row["files_left_out"],
            reads_refused=row["reads_refused"],
        )
