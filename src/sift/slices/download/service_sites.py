# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading a download's Site, its address and its outcome, shared by every part of the ledger."""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from sift.kernel.access.sites import site_for_key_on
from sift.kernel.db import Connection
from sift.kernel.ledger import Actor, Object, record_event
from sift.kernel.sorting import sort_key
from sift.kernel.vocabulary import VIA_DOWNLOAD, Subject
from sift.slices.download.art import creator_scope
from sift.slices.download.service_views import (
    _TERMINAL_LEDGER_STATES,
    SiteCount,
)
from sift.slices.download.sources.hosts import source_host
from sift.slices.download.sources.normalize import without_signature
from sift.slices.download.sources.registry import classify, match_site, site_key
from sift.slices.download.sources.sites import catalog as site_catalog

#: The Site ROW an address belongs to, found by the name the address is worked out to.
_SITE_ROW_BY_NAME = "SELECT id, name FROM sites WHERE name = ? COLLATE NOCASE"


def _flag(chosen: bool | None) -> int | None:
    """A paste's choice as the column stores it: 1, 0, or NULL for "follow the setting"."""
    return None if chosen is None else int(chosen)


def _choice(stored: object) -> bool | None:
    """The column read back. NULL (and anything the CHECK would not have let in) is no choice."""
    if stored == 1:
        return True
    if stored == 0:
        return False
    return None


def _shown_url(url: str | None) -> str | None:
    """The address as a person should read it, or None when that is the address itself.

    None rather than a copy, so a row carries the short form only when there is a short form worth
    carrying and the screen has one thing to test rather than two strings to compare.
    """
    if not url:
        return None
    shortened = without_signature(str(url))
    return shortened if shortened != url else None


def _site_of(url: str | None) -> str | None:
    """The site key an address belongs to, or None for one Sift has no record of.

    Worked out here rather than stored: it is derivable from the address, and a stored copy would be
    a second thing to keep true the first time a site gains a domain.
    """
    if not url:
        return None
    record = match_site(str(url))
    return record.key if record is not None else None


def site_home_of(url: str | None) -> str | None:
    """Where the site an address is on LIVES, for writing onto a Site the moment a download makes it."""
    if not url:
        return None
    record = site_catalog.match(str(url))
    if record is not None and record.hosts:
        return f"https://{record.hosts[0]}"
    host = source_host(str(url)).removeprefix("www.")
    return f"https://{host}" if "." in host else None


def _by_site(by_host: Mapping[str, int]) -> list[SiteCount]:
    """Hosts folded into the Sites they belong to, in the order a person reads names in.

    A host no Site claims is left out: it has no name to offer as a choice, and its rows are still
    in the list under every other narrowing.
    """
    sites: dict[str, int] = {}
    for host, many in by_host.items():
        name = _site_named(host)
        if name:
            sites[name] = sites.get(name, 0) + many
    return [
        SiteCount(name=name, count=many)
        for name, many in sorted(sites.items(), key=lambda one: sort_key(one[0]))
    ]


def site_name_of(url: str | None) -> str | None:
    """What the site is CALLED, worked out from the address the same way."""
    if not url:
        return None
    record = match_site(str(url))
    return record.site if record is not None else classify(str(url)).site


@lru_cache(maxsize=4096)
def _site_named(host: str) -> str | None:
    """The Site a HOST belongs to, by the one rule `site_name_of` uses for a whole address.

    Remembered per host: the catalog is fixed for the life of the process.
    """
    if not host:
        return None
    return site_name_of(f"https://{host}/")


def _creator_scope_of(url: str | None, username: str | None) -> str | None:
    """What this download's creator picture is filed under, if there could be one.

    Built here rather than in the browser so the shape of that name stays in one place: a client
    assembling it from two fields is a second copy of a rule, and the two drift.
    """
    if not url or not username:
        return None
    record = match_site(str(url))
    if record is None or record.profile_url is None or not record.username_is_a_person:
        return None
    return creator_scope(record.key, username)


def _subject_of(row: Any) -> Subject:
    """What a pause or a resume is ABOUT: the file it produced, or the download row itself.

    The file wherever there is one, since that is where somebody looks; otherwise the row stands
    in, under what it landed or the address as a person reads it.
    """
    if row["asset_id"] is not None:
        return Subject(kind="asset", id=str(row["asset_id"]), name=row["filename"])
    name = row["filename"] or _shown_url(row["url"]) or row["url"]
    return Subject(kind="download", id=str(row["id"]), name=None if name is None else str(name))


async def _record_outcome(
    connection: Connection,
    *,
    row: Any,
    landed: str | None = None,
    name: str | None = None,
    code: str | None = None,
) -> None:
    """Write down how a download ended: a file landed, or Sift gave up on it for good."""
    # `name` is the FILE'S name and is handed in rather than read off the row, because the row is
    # read before the write that puts it there: a landing writes the filename and the event in one
    # transaction, so the row this is given still says what it said a moment ago.
    filed = await _site_filed_on(connection, row["url"], site_name_of(row["url"]))
    site_id, site_name = filed if filed is not None else (None, None)
    site = (Subject(kind="site", id=site_id, name=site_name),) if site_id and site_name else ()
    if landed is not None:
        about: Subject = Subject(kind="asset", id=landed, name=name or row["filename"])
        payload = {"site": _site_of(row["url"]), "url": _shown_url(row["url"])}
        verb = "downloaded"
    else:
        shown = name or row["filename"] or _shown_url(row["url"]) or row["url"]
        about = Subject(
            kind="download", id=str(row["id"]), name=None if shown is None else str(shown)
        )
        payload = {"code": code}
        verb = "download_failed"
    asked_by = _requested_by(row) if landed is not None else None
    await record_event(
        connection,
        actor=Actor.sift(VIA_DOWNLOAD) if asked_by is None else Actor.user(asked_by),
        verb=verb,
        subject=(about, *site),
        object=None if site_id is None else Object(kind="site", id=site_id, name=site_name),
        payload=json.dumps(payload),
    )


async def _site_filed_on(
    connection: Connection, url: str | None, name: str | None
) -> tuple[str, str] | None:
    """The Site an address files under here, as its id and the name it has now, or None.

    By the site's key first (`download_sites`), because that survives the Site being renamed; by
    the name only for a site nothing has been filed from yet, which is the one case with no key
    remembered, and the name is then the catalog's own and the Site (if any) still carries it.
    """
    key = site_key(str(url)) if url else None
    if key is not None:
        filed = await site_for_key_on(connection, key)
        if filed is not None:
            return filed
    if name:
        found = list(await connection.execute_fetchall(_SITE_ROW_BY_NAME, (name,)))
        if found:
            return str(found[0]["id"]), str(found[0]["name"])
    return None


def _requested_by(row: Any) -> str | None:
    """The user a download row was asked for by, or None where nobody asked for it by name. The
    row is the whole of it (`SELECT *`), and every library has the column."""
    value = row["requested_by"]
    return None if value is None else str(value)


def _display_status(ledger_state: str, job_state: str | None) -> str:
    """What a screen shows, from the ledger row and the job that runs it."""
    if job_state == "blocked":
        return "blocked"
    if job_state == "paused":
        return "paused"
    if job_state == "failed" and ledger_state not in _TERMINAL_LEDGER_STATES:
        return "failed"
    return ledger_state
