# SPDX-License-Identifier: AGPL-3.0-or-later
"""The cookies saved for each Site: sealed on the way in, opened only for a download."""

from __future__ import annotations

import time
from dataclasses import dataclass

from sift.kernel.access import (
    by_user,
    ensure_site,
)
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor, record_event
from sift.kernel.vocabulary import Subject
from sift.slices.download.service_ledger import DownloadLedger
from sift.slices.download.service_views import (
    DOWNLOAD,
)
from sift.slices.download.sources import cookie_health
from sift.slices.download.sources.sites import catalog as site_catalog


@dataclass(frozen=True, slots=True)
class ConnectionView:
    """One site's saved cookies, as a screen sees it. Never the cookies, only that they are there."""

    id: str
    site: str
    status: str | None
    updated_at: int | None
    #: The first and last expiry in the jar, read when it was saved. Both, because the first one
    #: alone misrepresents any site with a challenge in front of it. See `CookieSummary`.
    expires_at: int | None = None
    expires_last: int | None = None
    #: When Sift last unsealed these cookies to use them, for a download or for a check.
    last_used_at: int | None = None
    #: The word a screen draws: saved, ending soon, or expired. Decided here rather than in the
    #: browser so one rule about dates and health exists, in one place. See `cookie_health.state_of`.
    state: str = cookie_health.STATE_SAVED


@dataclass(frozen=True, slots=True)
class ConnectionSecret:
    """Saved cookies' pointer to the sealed jar, for whatever is about to open it."""

    connection_id: str
    secret_id: str | None
    site: str | None = None


_LIST_CONNECTIONS = """
SELECT sc.id, p.name AS site, sc.status, sc.updated_at,
       sc.expires_at, sc.expires_last, sc.last_used_at
  FROM site_connections sc
  JOIN sites p ON p.id = sc.site_id
 ORDER BY COALESCE(p.name_sort, p.name), p.id
"""

_CONNECTION_BY_SITE = """
SELECT sc.id, sc.secret_id, sc.status
  FROM site_connections sc
  JOIN sites p ON p.id = sc.site_id
 WHERE p.name = ? COLLATE NOCASE
"""

#: The same row reached by its own id, with the site's name, for the check route: it is handed a
#: connection and has to say the site's name in the sentence it answers.
_CONNECTION_BY_ID = """
SELECT sc.id, sc.secret_id, p.name AS site
  FROM site_connections sc
  JOIN sites p ON p.id = sc.site_id
 WHERE sc.id = ?
"""

_SET_CONNECTION_STATUS = "UPDATE site_connections SET status = ?, updated_at = ? WHERE id = ?"

_CONNECTION_BY_SITE_ID = "SELECT id, secret_id FROM site_connections WHERE site_id = ?"

#: The row a forget is about, with the Site it belongs to: the sealed jar to destroy, and the name
#: and id the event needs. The name is read HERE rather than after the delete, because the row is
#: gone by then and a Site whose last connection has just been removed is exactly the case where a
#: name looked up later comes back empty.
_CONNECTION_LINK = """
SELECT sc.secret_id, sc.site_id, p.name AS site
  FROM site_connections sc
  JOIN sites p ON p.id = sc.site_id
 WHERE sc.id = ?
"""

_INSERT_CONNECTION = (
    "INSERT INTO site_connections"
    " (id, site_id, secret_id, status, updated_at, expires_at, expires_last)"
    " VALUES (?, ?, ?, 'saved', ?, ?, ?)"
)

#: A replacement writes the new jar's dates over the old ones and leaves `last_used_at` alone. The
#: dates describe the cookies, which have changed; the last use is a fact about this SITE that the
#: replacement did not undo, and blanking it would read as a site nothing has ever fetched from.
_UPDATE_CONNECTION = (
    "UPDATE site_connections"
    " SET secret_id = ?, status = 'saved', updated_at = ?, expires_at = ?, expires_last = ?"
    " WHERE id = ?"
)

#: Stamped where the jar is UNSEALED, which is the only place it is ever used.
_USED_NOW = "UPDATE site_connections SET last_used_at = ? WHERE secret_id = ?"

_DELETE_CONNECTION = "DELETE FROM site_connections WHERE id = ?"


class DownloadConnections(DownloadLedger):
    """Save, list, open and forget a Site's cookies, and what using them taught."""

    # --- The sites' saved cookies ----------------------------------------------------------

    async def list_connections(self) -> list[ConnectionView]:
        """Every site with saved cookies. Never the cookies, only what is known about them.

        The word each row is shown as is decided HERE, once, from the health and the last date in
        the jar. A client given the dates and asked to work it out would be a second copy of that
        rule, and the copy on the screen is the one nobody can see has gone wrong.

        The moment is read once for the whole list rather than per row, so two rows a millisecond
        apart cannot be judged against two different nows and disagree about the same week.
        """
        now = int(time.time())
        rows = await self._db.fetch_all(_LIST_CONNECTIONS)
        return [
            ConnectionView(
                id=row["id"],
                site=row["site"],
                status=row["status"],
                updated_at=row["updated_at"],
                expires_at=row["expires_at"],
                expires_last=row["expires_last"],
                last_used_at=row["last_used_at"],
                state=cookie_health.state_of(row["status"], row["expires_last"], now),
            )
            for row in rows
        ]

    async def save_connection(
        self,
        *,
        site: str,
        cookie: str,
        master_key: bytes,
        by: str,
        expires_at: int | None = None,
        expires_last: int | None = None,
    ) -> str:
        """Seal a site's cookies and attach them to its site. Returns the connection id.

        A replacement seals the new jar, points the connection at it and forgets the old secret.
        The cookies are never stored except sealed, and never returned by anything here. The two
        dates are the caller's reading of the jar. The event (`cookies_saved`, `cookies_replaced`)
        is about the Site, by `by`, in the same transaction, and carries nothing of the jar.
        """
        # A person made this site, named as its maker; where it lives comes from the catalog, and
        # the catalog's own spelling names the row. Only if this makes the row (`_ensure_site`).
        known = site_catalog.by_site(site)
        named = known.site if known is not None else site.strip()
        site_id = await ensure_site(
            self._db,
            named,
            made=by_user(by),
            address=f"https://{known.hosts[0]}" if known is not None and known.hosts else None,
        )
        secret_id = await self._secrets.seal(cookie.encode("utf-8"), master_key)
        now = int(time.time())
        # A fresh jar gets a clean slate, or the old jar's killswitch would keep the new one unsent.
        cookie_health.clear_for_site(site)
        # And every download waiting for cookies goes back in the line, as the sign-in path does;
        # every one, since a blocked row does not record which site it waited on.
        await self._queue.unblock(job_type=DOWNLOAD)

        existing = await self._db.fetch_one(_CONNECTION_BY_SITE_ID, (site_id,))
        subject = Subject(kind="site", id=site_id, name=named)
        if existing is None:
            connection_id = new_id()
            async with telling(self._db, EVERY_ADMIN, About.SETTINGS) as connection:
                await connection.execute(
                    _INSERT_CONNECTION,
                    (connection_id, site_id, secret_id, now, expires_at, expires_last),
                )
                await record_event(
                    connection,
                    actor=Actor.user(by),
                    verb="cookies_saved",
                    subject=subject,
                )
            return connection_id

        async with telling(self._db, EVERY_ADMIN, About.SETTINGS) as connection:
            await connection.execute(
                _UPDATE_CONNECTION, (secret_id, now, expires_at, expires_last, existing["id"])
            )
            await record_event(
                connection,
                actor=Actor.user(by),
                verb="cookies_replaced",
                subject=subject,
            )
        if existing["secret_id"] is not None:
            await self._secrets.forget(str(existing["secret_id"]))
        return str(existing["id"])

    async def delete_connection(self, connection_id: str, *, by: str) -> bool:
        """Forget a site's cookies and the sealed jar behind them. False if there was no such row.

        The one write here that deletes its own row, which is why the event matters more than it
        does next door: without it, forgetting a Site's cookies leaves nothing anywhere saying they
        were ever there, and "why does it no longer fetch from this Site" has no answer at all. See
        `save_connection` for the verbs and for the Site being the subject.
        """
        row = await self._db.fetch_one(_CONNECTION_LINK, (connection_id,))
        if row is None:
            return False
        async with telling(self._db, EVERY_ADMIN, About.SETTINGS) as connection:
            await connection.execute(_DELETE_CONNECTION, (connection_id,))
            await record_event(
                connection,
                actor=Actor.user(by),
                verb="cookies_forgotten",
                subject=Subject(kind="site", id=str(row["site_id"]), name=str(row["site"])),
            )
        if row["secret_id"] is not None:
            await self._secrets.forget(str(row["secret_id"]))
        return True

    async def connection_for_site(self, site: str) -> ConnectionSecret | None:
        """The saved cookies for a site, if there are any. The job asks this to know it needs a key."""
        row = await self._db.fetch_one(_CONNECTION_BY_SITE, (site,))
        if row is None:
            return None
        return ConnectionSecret(connection_id=row["id"], secret_id=row["secret_id"], site=site)

    async def connection_by_id(self, connection_id: str) -> ConnectionSecret | None:
        """One saved jar by its own id, with the site it belongs to. None when there is no such row."""
        row = await self._db.fetch_one(_CONNECTION_BY_ID, (connection_id,))
        if row is None:
            return None
        return ConnectionSecret(
            connection_id=str(row["id"]),
            secret_id=row["secret_id"],
            site=str(row["site"]),
        )

    async def record_login_health(self, site: str, status: str) -> None:
        """Write down what using the saved cookies just taught, so a screen can say they need
        replacing.
        """
        row = await self._db.fetch_one(_CONNECTION_BY_SITE, (site,))
        if row is None or row["status"] == status:
            return
        await self._say(
            _SET_CONNECTION_STATUS, (status, int(time.time()), row["id"]), About.SETTINGS
        )

    async def open_cookie(self, secret_id: str, master_key: bytes) -> str | None:
        """Unseal saved cookies with the master key, or None if they cannot be opened."""
        plaintext = await self._secrets.open(secret_id, master_key)
        if plaintext is None:
            return None
        await self._say(_USED_NOW, (int(time.time()), secret_id), About.SETTINGS)
        return plaintext.decode("utf-8", "replace")
