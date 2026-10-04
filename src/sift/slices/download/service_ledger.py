# SPDX-License-Identifier: AGPL-3.0-or-later
"""The job's side of the ledger: what is already fetched, whose a file is, and how a row ends."""

from __future__ import annotations

import time
from dataclasses import replace

from sift.kernel.access import (
    attribute_to_person,
    by_sift,
    link_asset_to_site,
    link_username_to_asset,
    seed_site_username,
)
from sift.kernel.access.sites import keep_site_key_on
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.db import Connection, Row
from sift.kernel.ledger import Object
from sift.kernel.vocabulary import VIA_DOWNLOAD
from sift.slices.download import naming
from sift.slices.download.service_base import (
    _GET_DOWNLOAD,
    _SET_STATE,
    _SET_STATE_FINISHED,
)
from sift.slices.download.service_controls import DownloadControls
from sift.slices.download.service_sites import (
    _choice,
    _record_outcome,
    _site_filed_on,
    site_home_of,
    site_name_of,
)
from sift.slices.download.service_views import (
    _INTERRUPTED,
    JobInput,
    PasteChoices,
    UsernameFrom,
)
from sift.slices.download.sources.registry import Attribution, site_key

#: Rows the job behind them has given up on, which nothing will ever settle from the inside.
_ORPHANED = """
SELECT d.id FROM downloads d
 WHERE d.state IN ('running','queued')
   AND (SELECT j.state FROM jobs j WHERE j.id = d.job_id) = 'failed'
"""

# A completed download counts as already-fetched whether it added new media ('done') or found it all
# already in the library ('duplicate'): either way this URL has been through, so a re-paste skips.
#
# Unless the file it produced has since been deleted. The ledger row survives a delete as history,
# with its `asset_id` cleared by the database, and a row that no longer points at anything is not a
# copy of anything: it must not answer "you already have this".
_DONE_BY_HASH = (
    "SELECT 1 FROM downloads WHERE url_hash = ? AND state IN ('done','duplicate')"
    " AND asset_id IS NOT NULL LIMIT 1"
)

# The same question one level down, for a link whose contents change. Same `asset_id IS NOT NULL`
# rule as the ledger above and for the same reason: a row whose file has since been deleted is not a
# record of holding anything, so the item becomes fetchable again by pasting the link.
_ITEM_DONE = (
    "SELECT 1 FROM download_items WHERE url_hash = ? AND media_key = ?"
    " AND asset_id IS NOT NULL LIMIT 1"
)

# Written only once the file has landed. `ON CONFLICT` because the same item can arrive twice from
# two different pastes of the same link, and the later arrival is the one that points at a file.
# Every file one paste produced, through the address hash the two tables share. Ordered by when
# each landed so a playlist reads in the order it was fetched.
_FILES_OF = (
    "SELECT di.asset_id FROM download_items di"
    " JOIN downloads d ON d.url_hash = di.url_hash"
    " WHERE d.id = ? AND di.asset_id IS NOT NULL"
    "\n -- ordered by the clock: an item has no id of its own; its key is the address and the"
    "\n -- name the Site gives the item (`media_key`)"
    "\n ORDER BY di.created_at, di.media_key"
)

_RECORD_ITEM = (
    "INSERT INTO download_items (url_hash, media_key, asset_id, created_at) VALUES (?, ?, ?, ?)"
    " ON CONFLICT(url_hash, media_key) DO UPDATE SET asset_id = excluded.asset_id"
)

_SET_DONE = (
    "UPDATE downloads SET state = ?, asset_id = ?, site = ?, username = ?, username_from = ?,"
    " filename = ?, finished_at = ?, seen_at = NULL, post_id = ?, title = ?, posted = ?,"
    " original = ?, files_offered = ?, files_left_out = ?, site_id = ?, person_id = ? WHERE id = ?"
)

#: The earlier rows of the same link whose file has since been deleted, given the file this one
#: landed. Held to the same name the row remembers, so a link whose contents change (a story tray,
#: a channel) never hands an old row a different file it never fetched.
_FOLLOW_THE_LINK = (
    "UPDATE downloads SET asset_id = ?"
    " WHERE url_hash = (SELECT url_hash FROM downloads WHERE id = ?) AND id <> ?"
    " AND state IN ('done', 'duplicate') AND asset_id IS NULL AND filename = ?"
)

#: The person a finished download's username on its Site belongs to, read the moment the row is
#: filed, which is the one moment the username the row names is certainly the one on the Site.
_PERSON_OF_USERNAME = (
    "SELECT person_id FROM usernames WHERE site_id = ? AND name = ? COLLATE NOCASE"
    " AND person_id IS NOT NULL ORDER BY id LIMIT 1"
)

_SET_FAILED = (
    "UPDATE downloads SET state = 'failed', error = ?, error_code = ?, error_tier = ?,"
    " finished_at = ?, seen_at = NULL WHERE id = ?"
)

_SET_QUARANTINED = (
    "UPDATE downloads SET state = 'quarantined', error = ?, finished_at = ?, seen_at = NULL"
    " WHERE id = ?"
)

_SET_VIA = "UPDATE downloads SET via = ?, via_address = ? WHERE id = ?"

_SET_FOLDER = "UPDATE downloads SET folder_id = ? WHERE id = ?"


class DownloadLedger(DownloadControls):
    """What the download job reads and writes as it runs and ends."""

    # --- The ledger's write side (the job calls these) -----------------------------------

    async def is_already_done(self, hash_value: str) -> bool:
        """Whether this URL has already been downloaded. The skip-a-re-drop check."""
        return await self._db.fetch_one(_DONE_BY_HASH, (hash_value,)) is not None

    async def files_of(self, download_id: str) -> list[str]:
        """Every file this download produced that is still in the library, oldest first."""
        rows = await self._db.fetch_all(_FILES_OF, (download_id,))
        return [str(row["asset_id"]) for row in rows]

    async def item_already_done(self, hash_value: str, media_key: str) -> bool:
        """Whether one piece of media behind this link has already been fetched and kept.

        Asked per item, and only for a link whose contents change. The link-level check is the wrong
        question for those: it is still true tomorrow, when there is more behind the link than there
        was.
        """
        return await self._db.fetch_one(_ITEM_DONE, (hash_value, media_key)) is not None

    async def record_item(self, hash_value: str, media_key: str, *, asset_id: str) -> None:
        """Record that one piece of media behind a link has landed.

        Called after the file has been through the import gate, never before. Recorded at resolve
        time it would mark a download that then failed as fetched, and the next paste of the link
        would skip the very item that never arrived.
        """
        # Nothing announces this: it is the ledger remembering which pieces of a gallery it has
        # already taken, so a re-run skips them. No screen draws it.
        await self._db.execute(_RECORD_ITEM, (hash_value, media_key, asset_id, int(time.time())))

    async def job_input(self, download_id: str) -> JobInput | None:
        """The URL and destination for a queued download, read from the ledger row.

        The job payload carries only the row id; the URL and the drop target live in the row and are
        read here. A payload that carried the URL would put it in every log line the job appears in.
        """
        row = await self._db.fetch_one(
            "SELECT url, dest_folder_id, remember FROM downloads WHERE id = ?",
            (download_id,),
        )
        if row is None:
            return None
        return JobInput(
            url=str(row["url"]),
            dest_folder_id=row["dest_folder_id"],
            choices=PasteChoices(remember=_choice(row["remember"])),
        )

    async def attribute(
        self,
        *,
        asset_id: str,
        site: str,
        username: str | None,
        username_is_a_person: bool = False,
        may_create_people: bool = True,
        address: str | None = None,
    ) -> None:
        """Record where a downloaded file came from, and who it is of."""
        site_address = site_home_of(address)
        if username is None:
            # Nothing but the site to record, which is still worth recording.
            site_id = await link_asset_to_site(
                self._db,
                asset_id=asset_id,
                site=site,
                made=by_sift(VIA_DOWNLOAD),
                site_address=site_address,
            )
            await self._keep_site_key(address, site_id)
            await self._reindexer.touched(asset_id)
            return

        site_id, username_id = await seed_site_username(
            self._db,
            site=site,
            name=username,
            made=by_sift(VIA_DOWNLOAD),
            site_address=site_address,
        )
        await self._keep_site_key(address, site_id)
        # THE one write that puts this file on that site. Everything else here is about the human.
        await link_username_to_asset(
            self._db, asset_id=asset_id, username_id=username_id, source=VIA_DOWNLOAD
        )
        if username_is_a_person:
            await attribute_to_person(
                self._db,
                asset_id=asset_id,
                name=username,
                # The row this username just got, so resolving the person settles the username.
                username_id=username_id,
                create_if_unknown=may_create_people,
                # Nobody asked for this person: an address was pasted and named them.
                made=by_sift(VIA_DOWNLOAD),
            )
        # The username is indexed text written AFTER the import, so the file is indexed again.
        await self._reindexer.touched(asset_id)

    async def _keep_site_key(self, address: str | None, site_id: str) -> None:
        """Remember which Site this address's site files under, the first time it files anywhere.

        Not announced: nothing on a screen draws it. It is what `filed_as` reads to find the Site
        again after somebody renames it.
        """
        key = site_key(address) if address else None
        if key is None:
            return
        async with self._db.write() as connection:
            await keep_site_key_on(connection, key, site_id)

    async def filed_as(self, url: str, attribution: Attribution) -> Attribution:
        """What an address says about its origin, with the site named as ITS Site is named here."""
        key = site_key(url)
        if key is None or attribution.site is None:
            return attribution
        filed = await self._filed_under(key)
        if filed is None:
            return attribution
        return replace(attribution, site=filed[1])

    async def seed_music(self, asset_id: str, music: str, *, url: str) -> bool:
        """Record the track a page said a file is set to, and only if nothing is there yet. Whether
        it was written.
        """
        page: Object | None = None
        async with self._db.read() as connection:
            filed = await _site_filed_on(connection, url, site_name_of(url))
        if filed is not None:
            page = Object(kind="site", id=filed[0], name=filed[1])
        return await self._content.seed_music(asset_id, music, page=page)

    async def mark_running(self, download_id: str) -> None:
        await self._say(_SET_STATE, ("running", download_id), About.DOWNLOADS)

    async def mark_skipped(self, download_id: str) -> None:
        await self._say(
            _SET_STATE_FINISHED, ("skipped", int(time.time()), download_id), About.DOWNLOADS
        )

    async def mark_done(
        self,
        download_id: str,
        *,
        asset_id: str | None,
        site: str | None,
        username: str | None,
        filename: str | None = None,
        was_duplicate: bool = False,
        username_from: UsernameFrom | None = None,
        named_from: naming.Facts | None = None,
        offered: int = 0,
        left_out: int = 0,
    ) -> None:
        """Record a finished download: `duplicate` where every file was already in the library.

        `asset_id` is absent only for a changing link with nothing new behind it, which settles as
        done, never as a duplicate. `username_from` is None where nobody was named. `named_from`
        feeds the naming preview; `offered` and `left_out` are kept only where a file was left out.
        """
        state = "duplicate" if was_duplicate else "done"
        # Read before it is written: the event works the Site out of the address.
        row = await self._db.fetch_one(_GET_DOWNLOAD, (download_id,))
        async with telling(self._db, EVERY_ADMIN, About.DOWNLOADS) as connection:
            site_id, person_id = await _filed_ids(connection, row, site, username)
            await connection.execute(
                _SET_DONE,
                (
                    state,
                    asset_id,
                    site,
                    username,
                    None if username is None else username_from,
                    filename,
                    int(time.time()),
                    None if named_from is None else named_from.id,
                    None if named_from is None else named_from.title,
                    None
                    if named_from is None or named_from.posted is None
                    else named_from.posted.isoformat(),
                    None if named_from is None else named_from.original,
                    offered if left_out else None,
                    left_out or None,
                    site_id,
                    person_id,
                    download_id,
                ),
            )
            # THE SAME LINK, FETCHED AGAIN after its file was deleted: the earlier row leads to the
            # file that is in the library again rather than reading as gone.
            if asset_id is not None and filename is not None:
                await connection.execute(
                    _FOLLOW_THE_LINK, (asset_id, download_id, download_id, filename)
                )
            # A FILE LANDED, and the record says so in the same transaction, only where one did.
            if row is not None and asset_id is not None and not was_duplicate:
                await _record_outcome(connection, row=row, landed=asset_id, name=filename)

        # And the file learns where it came from, once, as a field on its record; the ledger's own
        # address stays exactly what was fetched, since it is hashed into the re-drop key.
        if asset_id is not None and row is not None and row["url"]:
            await self._content.seed_download_url(asset_id, str(row["url"]))

    async def settle_orphans(self) -> int:
        """Settle every ledger row whose job gave up without telling it. Returns how many."""
        stuck = [str(row["id"]) for row in await self._db.fetch_all(_ORPHANED)]
        for download_id in stuck:
            await self.mark_failed(download_id, error=_INTERRUPTED)
        # The count goes back to the caller rather than into a log from here. This module has never
        # logged, and whoever asks for this is at boot, which is where saying so belongs.
        return len(stuck)

    async def mark_failed(
        self,
        download_id: str,
        *,
        error: str,
        code: str | None = None,
        tier: int | None = None,
    ) -> None:
        """Record a failure. The sentence is what is read; the code and tier are what is searched."""
        # The row before the write, for the address the event names its Site by. A row that is not
        # there any more is still written to (the statement matches nothing and nothing is
        # announced) and records no event: an event about a queue row that has gone is an event
        # nobody can reach from anywhere.
        row = await self._db.fetch_one(_GET_DOWNLOAD, (download_id,))
        async with telling(self._db, EVERY_ADMIN, About.DOWNLOADS) as connection:
            await connection.execute(
                _SET_FAILED, (error, code, tier, int(time.time()), download_id)
            )
            if row is not None:
                await _record_outcome(connection, row=row, code=code)

    async def record_route(self, download_id: str, via: str, *, address: str | None = None) -> None:
        """Which way out this download actually went, in the words a person reads."""
        await self._say(_SET_VIA, (via, address, download_id), About.DOWNLOADS)

    async def record_folder(self, download_id: str, folder_id: str | None) -> None:
        """The folder this download's job resolved, written when it resolved it."""
        await self._say(_SET_FOLDER, (folder_id, download_id), About.DOWNLOADS)

    async def mark_quarantined(self, download_id: str, *, error: str) -> None:
        """The file the site served was quarantined by the gate rather than added to the library.

        Told apart from a failure because it is a different event with a different answer. Nothing
        went wrong with the download: it arrived, in full, and what arrived was not what it was
        supposed to be. A red failure invites somebody to try again, which will fetch the same file
        and set it aside again.
        """
        await self._say(_SET_QUARANTINED, (error, int(time.time()), download_id), About.DOWNLOADS)


async def _filed_ids(
    connection: Connection, row: Row | None, site: str | None, username: str | None
) -> tuple[str | None, str | None]:
    """The Site and the person a finished row was filed under, read in its own transaction, so the
    row leads to them whatever either is called later."""
    filed = await _site_filed_on(connection, row["url"] if row else None, site)
    site_id = filed[0] if filed is not None else None
    person_id: str | None = None
    if site_id is not None and username is not None:
        owner = list(await connection.execute_fetchall(_PERSON_OF_USERNAME, (site_id, username)))
        person_id = str(owner[0]["person_id"]) if owner else None
    return site_id, person_id
