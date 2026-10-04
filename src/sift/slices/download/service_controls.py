# SPDX-License-Identifier: AGPL-3.0-or-later
"""The presses on one row: cancel, pause, resume, retry, hide, restore and promote."""

from __future__ import annotations

import time

from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.jobs.tuning import PRIORITY_MIN
from sift.kernel.ledger import Actor, record_event
from sift.slices.download.service_base import (
    _GET_DOWNLOAD,
    _JOB_STATE,
    _SET_STATE,
    _SET_STATE_FINISHED,
)
from sift.slices.download.service_listing import DownloadListing
from sift.slices.download.service_sites import (
    _subject_of,
)
from sift.slices.download.service_views import (
    _PAUSABLE_LEDGER_STATES,
    _TERMINAL_LEDGER_STATES,
)

# Move a queued download to the front. The queue runs the lowest priority first, so this is the
# smallest number the queue accepts rather than a flag of its own.
#
# It writes to the job table this file already reads from, which is a boundary worth naming out
# loud: this slice owns its own jobs and nothing else's, and the WHERE clause says so: the type is
# pinned, so this can only ever move a download.
_PROMOTE = (
    "UPDATE jobs SET priority = ? WHERE type = 'download' AND state = 'queued'"
    " AND id = (SELECT job_id FROM downloads WHERE id = ?)"
)

_JOB_FOR_DOWNLOAD = "SELECT job_id AS id FROM downloads WHERE id = ? AND job_id IS NOT NULL"

#: Remove from the list. The row stays (it is what stops a re-pasted link being fetched twice)
#: and only stops being drawn in the queue.
_HIDE = "UPDATE downloads SET hidden_at = ? WHERE id = ?"

#: And the undo of it. Only a row that IS put away, so the statement itself says whether there was
#: anything to put back: the count of rows it changed is the answer, and a read first would be a
#: second question with a moment in between for the answer to change.
_RESTORE = "UPDATE downloads SET hidden_at = NULL WHERE id = ? AND hidden_at IS NOT NULL"

#: What a row IS before it can be put away. A queued, running or blocked download is still
#: happening, and a screen that made it disappear would leave something fetching with nowhere to
#: watch it or stop it. The state read here is the LEDGER's; the job's blocked state is folded in
#: by the caller, which is the one that has both.
_STATE_OF_DOWNLOAD = "SELECT state FROM downloads WHERE id = ?"


class DownloadControls(DownloadListing):
    """What a person can do to one download from its row."""

    async def cancel(self, download_id: str, *, by: str) -> bool:
        """Cancel a queued or running download: stop the job that runs it and mark the ledger row."""
        row = await self._db.fetch_one(_GET_DOWNLOAD, (download_id,))
        if row is None or row["state"] in _TERMINAL_LEDGER_STATES:
            return False
        job = await self._db.fetch_one(_JOB_FOR_DOWNLOAD, (download_id,))
        if job is not None:
            await self._queue.cancel(str(job["id"]))
        async with telling(self._db, EVERY_ADMIN, About.DOWNLOADS) as connection:
            await connection.execute(
                _SET_STATE_FINISHED, ("canceled", int(time.time()), download_id)
            )
            await record_event(
                connection,
                actor=Actor.user(by),
                verb="canceled",
                subject=_subject_of(row),
            )
        return True

    async def hide(self, download_id: str) -> str | None:
        """Take a settled download out of the list. Returns its ledger state when it refuses."""
        row = await self._db.fetch_one(_STATE_OF_DOWNLOAD, (download_id,))
        if row is None:
            return None
        state = str(row["state"])
        if state not in _TERMINAL_LEDGER_STATES:
            return state
        await self._say(_HIDE, (int(time.time()), download_id), About.DOWNLOADS)
        return None

    async def pause(self, download_id: str, *, by: str) -> bool:
        """Stop a download where it is, keeping what has arrived. False if it was not running."""
        row = await self._db.fetch_one(_GET_DOWNLOAD, (download_id,))
        if row is None or str(row["state"]) not in _PAUSABLE_LEDGER_STATES:
            return False
        job = await self._db.fetch_one(_JOB_FOR_DOWNLOAD, (download_id,))
        if job is not None:
            await self._queue.pause(str(job["id"]))
        async with telling(self._db, EVERY_ADMIN, About.DOWNLOADS) as connection:
            await connection.execute(_SET_STATE, ("paused", download_id))
            await record_event(
                connection,
                actor=Actor.user(by),
                verb="paused",
                subject=_subject_of(row),
            )
        return True

    async def resume(self, download_id: str, *, by: str) -> bool:
        """Put a paused download back in the line. False if it was not paused."""
        row = await self._db.fetch_one(_GET_DOWNLOAD, (download_id,))
        if row is None:
            return False
        job = await self._db.fetch_one(_JOB_FOR_DOWNLOAD, (download_id,))
        # Paused is what the row shows, and the row folds the job's state in: a job that paused
        # itself, or one whose pause landed after the route had already moved on, is a paused row
        # whose ledger word never changed, and its Resume must not answer "not paused".
        job_state = None
        if job is not None:
            found = await self._db.fetch_one(_JOB_STATE, (str(job["id"]),))
            job_state = str(found["state"]) if found is not None else None
        if str(row["state"]) != "paused" and job_state != "paused":
            return False
        async with telling(self._db, EVERY_ADMIN, About.DOWNLOADS) as connection:
            await connection.execute(_SET_STATE, ("queued", download_id))
            await record_event(
                connection,
                actor=Actor.user(by),
                verb="resumed",
                subject=_subject_of(row),
            )
        if job is not None:
            await self._queue.resume(str(job["id"]))
        else:
            await self._queue_for(download_id, {"download_id": download_id})
        return True

    async def restore(self, download_id: str) -> bool:
        """Put a removed row back in the list. False if it was not one that had been removed."""
        async with telling(self._db, EVERY_ADMIN, About.DOWNLOADS) as connection:
            before = connection.total_changes
            await connection.execute(_RESTORE, (download_id,))
            return connection.total_changes != before

    async def promote(self, download_id: str) -> bool:
        """Put a queued download at the front of the queue. False if it was not queued any more.

        Only a queued one. A download that has started cannot be made to have started earlier, and
        one that has finished has nowhere to be moved to, so this quietly does nothing rather than
        appearing to reorder something that is not waiting.
        """
        row = await self._db.fetch_one(_GET_DOWNLOAD, (download_id,))
        if row is None or row["state"] != "queued":
            return False
        await self._say(_PROMOTE, (PRIORITY_MIN, download_id), About.DOWNLOADS)
        return True

    async def retry(self, download_id: str) -> bool:
        """Put a failed download back in the queue, as itself rather than as a second row.

        The same row, for the reason fetching-anyway uses the same row: this is one link with one
        history, and a second row would leave two records of it with two different endings.
        """
        row = await self._db.fetch_one(_GET_DOWNLOAD, (download_id,))
        if row is None or row["state"] not in {"failed", "canceled", "quarantined"}:
            return False
        await self._say(_SET_STATE, ("queued", download_id), About.DOWNLOADS)
        await self._queue_for(download_id, {"download_id": download_id})
        return True
