# SPDX-License-Identifier: AGPL-3.0-or-later
"""The download ledger's base: what a Site is filed under, the bell it rings, and a paste queued."""

from __future__ import annotations

import time
from datetime import UTC, date, datetime
from typing import Any

from sift.kernel.access.sites import site_for_key_on
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce_now
from sift.kernel.content import ContentStore
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.jobs import WAITED_ON_PRIORITY, JobQueue
from sift.kernel.seams import ReindexSeam
from sift.slices.download import naming
from sift.slices.download.secrets import SecretStore
from sift.slices.download.service_sites import (
    _flag,
)
from sift.slices.download.service_views import (
    DOWNLOAD,
    FOLLOW_THE_SETTINGS,
    PasteChoices,
)
from sift.slices.download.sources import url_hash

_INSERT_DOWNLOAD = (
    "INSERT INTO downloads"
    " (id, url, url_hash, dest_folder_id, aimed_kind, aimed_id, aimed_by, created_at,"
    " remember, requested_by)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

#: What a dropped link was aimed at, read back when the file has landed. Three columns and nothing
#: else: whether the target still exists is the filing seam's question, asked at the moment of
#: filing rather than now, because minutes pass in between.
_AIM_OF_DOWNLOAD = "SELECT aimed_kind, aimed_id, aimed_by FROM downloads WHERE id = ?"

_GET_DOWNLOAD = "SELECT * FROM downloads WHERE id = ?"

# Two shapes for setting a state, and which one is used says whether the download has STOPPED.
_SET_STATE = "UPDATE downloads SET state = ?, finished_at = NULL WHERE id = ?"

#
# Every write that ENDS a row clears `seen_at` as well: an ending is what the Downloads rail's dot
# is about, so a row that ends (for the first time, or again after a retry) is unseen until
# somebody looks. See `rail`.
_SET_STATE_FINISHED = "UPDATE downloads SET state = ?, finished_at = ?, seen_at = NULL WHERE id = ?"

_URL_FOR_DOWNLOAD = "SELECT id FROM downloads WHERE id = ?"

#: The row is told its job when the job is queued. Not announced: the job's own queueing is what
#: the screens hear, and the row's state has not moved.
_SET_JOB = "UPDATE downloads SET job_id = ? WHERE id = ?"

_JOB_STATE = "SELECT state FROM jobs WHERE id = ?"

#: The creator of the newest finished download from a Site, for a Site whose rows predate the
#: naming facts. Newest by id, which is the order the rows were made in. By the Site's id where the
#: site files somewhere (`site_for_key_on`), so a renamed Site keeps its example; by the word the
#: rows kept only for a site nothing has been filed from, where the word is the catalog's own.
_NEWEST_CREATOR = (
    "SELECT username FROM downloads WHERE site = ? AND state IN ('done', 'duplicate')"
    " AND username IS NOT NULL ORDER BY id DESC LIMIT 1"
)

_NEWEST_CREATOR_OF_SITE = (
    "SELECT username FROM downloads WHERE site_id = ? AND state IN ('done', 'duplicate')"
    " AND username IS NOT NULL ORDER BY id DESC LIMIT 1"
)

#: What the newest finished download from a Site was named FROM (`original` is written with the
#: rest of the facts, so a row that has it has them all, each still None where the Site said none).
#: By the Site's id or the word, as above.
_NEWEST_NAMED = (
    "SELECT site, username, original, post_id, title, posted, finished_at FROM downloads"
    " WHERE site = ? AND state IN ('done', 'duplicate') AND original IS NOT NULL"
    " ORDER BY id DESC LIMIT 1"
)

_NEWEST_NAMED_OF_SITE = (
    "SELECT site, username, original, post_id, title, posted, finished_at FROM downloads"
    " WHERE site_id = ? AND state IN ('done', 'duplicate') AND original IS NOT NULL"
    " ORDER BY id DESC LIMIT 1"
)


class DownloadBase:
    """The ledger's handles, its one write door, and putting a link in the line."""

    def __init__(
        self,
        database: Database,
        queue: JobQueue,
        secrets: SecretStore,
        reindexer: ReindexSeam,
        content: ContentStore,
    ) -> None:
        self._db = database
        self._queue = queue
        self._secrets = secrets
        self._reindexer = reindexer
        # Held for exactly one thing: writing the address a finished download came from onto the
        # file's own record. `assets` is the kernel's table and this slice may not write SQL
        # against it, so the write goes through the store that owns it. Nothing here READS an asset
        # through this: reads are the access layer's, behind the permission check, always.
        self._content = content

    async def _filed_under(self, key: str) -> tuple[str, str] | None:
        """The Site the site `key` names files under here, as its id and its name now."""
        async with self._db.read() as connection:
            return await site_for_key_on(connection, key)

    async def site_name_now(self, key: str, site: str) -> str:
        """What the Site the site `key` names files under is called here now, else `site`, the
        catalog's name for it (nothing has been filed from it yet)."""
        filed = await self._filed_under(key)
        return site if filed is None else filed[1]

    async def newest_named(self, key: str, site: str) -> naming.Facts | None:
        """What the newest finished download from this Site was named from, or None where no row
        from it keeps that yet: the naming preview's wholly real example.

        `key` is the site's catalog key and `site` the catalog's name for it. The example names the
        Site the way it is named here now, which is the name a download from it files under.
        """
        filed = await self._filed_under(key)
        if filed is None:
            row = await self._db.fetch_one(_NEWEST_NAMED, (site,))
        else:
            row = await self._db.fetch_one(_NEWEST_NAMED_OF_SITE, (filed[0],))
        if row is None:
            return None
        said = row["posted"]
        posted: date | datetime | None = None
        if isinstance(said, str) and said:
            posted = date.fromisoformat(said) if len(said) == 10 else datetime.fromisoformat(said)
        finished = row["finished_at"]
        return naming.Facts(
            site=row["site"] if filed is None else filed[1],
            username=row["username"],
            original=str(row["original"]),
            when=None if finished is None else datetime.fromtimestamp(int(finished), UTC),
            id=row["post_id"],
            title=row["title"],
            posted=posted,
        )

    async def newest_creator(self, key: str, site: str) -> str | None:
        """The creator of the newest finished download from this Site, or None where none names
        one: what the naming preview shows in place of an invented creator. Found as
        `newest_named` finds its row."""
        filed = await self._filed_under(key)
        if filed is None:
            row = await self._db.fetch_one(_NEWEST_CREATOR, (site,))
        else:
            row = await self._db.fetch_one(_NEWEST_CREATOR_OF_SITE, (filed[0],))
        return None if row is None else str(row["username"])

    async def _say(self, sql: str, params: tuple[object, ...], about: About) -> None:
        """Write to the ledger, and tell the screens that draw it."""
        await self._db.execute(sql, params)
        announce_now(EVERY_ADMIN, about)

    # --- The URL door (capture calls this; so does the drop-on-a-folder route) -----------

    async def submit_url(
        self,
        *,
        url: str,
        dest_folder_id: str | None = None,
        aimed_kind: str | None = None,
        aimed_id: str | None = None,
        aimed_by: str | None = None,
        choices: PasteChoices = FOLLOW_THE_SETTINGS,
        requested_by: str | None = None,
    ) -> str:
        """Record a download and queue the job that runs it. Returns the ledger row id."""
        download_id = new_id()
        await self._say(
            _INSERT_DOWNLOAD,
            (
                download_id,
                url,
                url_hash(url),
                dest_folder_id,
                aimed_kind,
                aimed_id,
                aimed_by,
                int(time.time()),
                _flag(choices.remember),
                requested_by,
            ),
            About.DOWNLOADS,
        )
        await self._queue_for(download_id, {"download_id": download_id})
        return download_id

    async def _queue_for(self, download_id: str, payload: dict[str, Any]) -> None:
        """Queue the job that runs this row, and write its id on the row.

        Every job a row gets is queued through here, so the row always names its newest job,
        which is the one whose state the screen shows and the one a cancel has to reach.
        """
        job_id = await self._queue.enqueue(DOWNLOAD, payload, priority=WAITED_ON_PRIORITY)
        await self._say(_SET_JOB, (job_id, download_id), About.DOWNLOADS)

    async def aim_of(self, download_id: str) -> tuple[str, str, str] | None:
        """Where this download was aimed, or None when it was not aimed anywhere.

        All three or nothing: a row carrying a kind and no id, or an id and no user, is a row
        nothing can act on, and treating it as "not aimed" is the reading that cannot go wrong.
        """
        row = await self._db.fetch_one(_AIM_OF_DOWNLOAD, (download_id,))
        if row is None:
            return None
        kind, target, user_id = row["aimed_kind"], row["aimed_id"], row["aimed_by"]
        if not kind or not target or not user_id:
            return None
        return str(kind), str(target), str(user_id)

    async def fetch_anyway(self, download_id: str) -> bool:
        """Run a skipped download again, without the ledger's opinion. False if there is no such row.

        The same row rather than a second one: what somebody is doing here is disagreeing with the
        answer this row already gave, and a new row would leave two records of one link with two
        different endings. The row goes back to queued and the job is told, once, to pass step one.
        """
        if await self._db.fetch_one(_URL_FOR_DOWNLOAD, (download_id,)) is None:
            return False
        await self._say(_SET_STATE, ("queued", download_id), About.DOWNLOADS)
        await self._queue_for(download_id, {"download_id": download_id, "ignore_ledger": True})
        return True
