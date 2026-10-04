# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sites and usernames: the upserts that make them, and filing a file under one."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from sift.kernel import site_icons
from sift.kernel.access.catalog.made import Made, _clean_username
from sift.kernel.access.catalog.people import link_username_to_person
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.db import Connection, Database
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor as LedgerActor
from sift.kernel.ledger import Object as LedgerObject
from sift.kernel.ledger import record_event
from sift.kernel.sorting import sort_key
from sift.kernel.text import clean_stored_text, clean_token_text
from sift.kernel.vocabulary import Subject as LedgerSubject

# A site is matched by its NOCASE-unique name, and `DO NOTHING` keeps the first answer, so a second
# sighting restamps nothing. The maker is bound (`Made`): a download is Sift, a site added by hand
# is a person. `RETURNING` tells the caller the row is new, which is when its address is written.
_INSERT_SITE = (
    "INSERT INTO sites"
    " (id, name, name_sort, created_at, created_by_kind, created_by_via, created_by_user_id)"
    " VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(name) DO NOTHING RETURNING id"
)
_SELECT_SITE = "SELECT id FROM sites WHERE name = ?"

#: A site made with an address starts its list with it: a Site's address is the first row of that
#: list by id (`sites.SITE_ADDRESS`).
_INSERT_SITE_ADDRESS = (
    "INSERT INTO site_links (id, site_id, url, label, created_at) VALUES (?, ?, ?, NULL, ?)"
    " ON CONFLICT(site_id, url) DO NOTHING"
)


def site_home(address: str | None) -> str | None:
    """The site's own address out of any address on it: the scheme and the host, nothing else.

    A deep address's path, query and signature are not the site's to keep. None for anything that
    is not an http(s) address with a dotted host."""
    if not address:
        return None
    cleaned = address.strip()
    try:
        parts = urlsplit(cleaned if "//" in cleaned else f"https://{cleaned}")
        host = (parts.hostname or "").lower()
    except ValueError:  # an unclosed `[` reads as a broken IPv6 literal: there is no host
        return None
    scheme = (parts.scheme or "https").lower()
    if scheme not in {"http", "https"} or "." not in host.strip("."):
        return None
    return f"{scheme}://{host}"


# Unique on (site_id, name). A later sighting fills a display name or URL only where it is still
# empty: `COALESCE(existing, new)` never overwrites one already set.
_INSERT_USERNAME = """
INSERT INTO usernames (id, site_id, name, name_sort, display_name, url, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(site_id, name) DO UPDATE SET
  display_name = COALESCE(usernames.display_name, excluded.display_name),
  url          = COALESCE(usernames.url, excluded.url)
"""
_SELECT_USERNAME = "SELECT id FROM usernames WHERE site_id = ? AND name = ?"

#: The username this site calls by this number, whatever it calls itself today.
_SELECT_USERNAME_BY_NUMBER = "SELECT id, name FROM usernames WHERE site_id = ? AND number = ?"

#: What the site calls it now. Only ever fills a blank: a username's number never changes, so a
#: row that already has one and a fetch that disagrees is not something to overwrite quietly.
_SET_USERNAME_NUMBER = "UPDATE usernames SET number = ? WHERE id = ? AND number IS NULL"

#: The username was renamed on the site. The number found it; the stored name catches up.
_RENAME_USERNAME = "UPDATE usernames SET name = ?, name_sort = ? WHERE id = ?"

# Idempotent by its primary key. `DO NOTHING` keeps the first answer to who decided and when
# (`source`, `decided_at`): a file somebody filed by hand stays theirs when an import later agrees,
# and a re-run never restamps a filing with today.
_LINK_ASSET_USERNAME = (
    "INSERT INTO asset_usernames (asset_id, username_id, source, decided_at, box_id)"
    " VALUES (?, ?, ?, ?, ?) "
    "ON CONFLICT(asset_id, username_id) DO NOTHING"
)


async def _ensure_site(
    connection: Connection, name: str, *, made: Made, address: str | None = None
) -> str:
    """Upsert a site by name on an open connection, returning its id.

    The name is cleaned before the insert and the read-back alike. `made` and `address` (trimmed to
    the site's own, `site_home`) are written only where the row is created, never over a sighting.
    """
    cleaned = clean_token_text(name).strip()
    home = site_home(address)
    now = int(time.time())
    made_now = await (
        await connection.execute(
            _INSERT_SITE,
            (
                new_id(),
                cleaned,
                sort_key(cleaned),
                now,
                made.kind,
                made.via,
                made.user_id,
            ),
        )
    ).fetchone()
    if made_now is not None:
        if home is not None:
            await connection.execute(_INSERT_SITE_ADDRESS, (new_id(), made_now["id"], home, now))
        return str(made_now["id"])
    row = await (await connection.execute(_SELECT_SITE, (cleaned,))).fetchone()
    return str(row["id"])  # type: ignore[index]


#: Every address each Site holds for itself. Read whole and filtered in Python, because "is this
#: address on that host" needs a URL parser, and a library holds hundreds of Sites.
_SITE_ADDRESSES = """
SELECT s.id AS id, s.name AS name, l.url AS url FROM site_links l JOIN sites s ON s.id = l.site_id
ORDER BY 1, l.id
"""


async def site_on_host(connection: Connection, address: str) -> str | None:
    """The NAME of the Site in this library whose own address is on this address's host, or None.

    Only a Site's own address counts (`site_icons.is_a_sites_own`), never its links to pages on
    other sites; a domain above the host matches too, the first Site by id wins, and a Site named as
    a label never does (`site_icons.is_a_label`)."""
    host = site_icons.host_of(address)
    if not host:
        return None
    for row in await connection.execute_fetchall(_SITE_ADDRESSES):
        held = str(row["url"] or "")
        # A row called by a box's word for a kind of link is not a site, wherever it says it lives.
        if not site_icons.is_a_sites_own(held) or site_icons.is_a_label(str(row["name"])):
            continue
        own = site_icons.host_of(held)
        if own and (host == own or host.endswith("." + own)):
            return str(row["name"])
    return None


async def site_for_address(connection: Connection, address: str) -> str | None:
    """What the Site an address belongs to is called: the pack's name, else the library's own Site.

    None means no Site Sift knows: the caller keeps a plain link rather than inventing one."""
    return site_icons.name_for(address) or await site_on_host(connection, address)


async def site_for(db: Database, address: str) -> str | None:
    """`site_for_address`, for a caller holding the database rather than a connection."""
    async with db.read() as connection:
        return await site_for_address(connection, address)


#: A Site by its name without case, and the username of this name on it that has a file filed under
#: it, where there is one.
_SELECT_FILED_USERNAME = """
SELECT s.id AS site_id, s.name AS site, u.id AS username_id, u.name AS username
FROM sites s LEFT JOIN usernames u ON u.id = (
  SELECT one.id FROM usernames one WHERE one.site_id = s.id AND one.name = ? COLLATE NOCASE
    AND EXISTS (SELECT 1 FROM asset_usernames au WHERE au.username_id = one.id)
  ORDER BY one.id LIMIT 1)
WHERE s.name = ? COLLATE NOCASE ORDER BY s.id LIMIT 1
"""


@dataclass(frozen=True, slots=True)
class FiledAt:
    """Where an address points in this library: its Site, and the username a file is filed under.

    An id is None where there is no such row; the names are the stored spellings.
    """

    site_id: str | None
    username_id: str | None
    site: str = ""
    username: str = ""


async def filed_username(db: Database, *, site: str, name: str) -> FiledAt:
    """This Site's id, and this username's on it when a file is filed under it.

    A username on a site exists only when a file is filed under it: an address alone (a box listing
    a person's pages) is a link on their record, never a username. So a writer holding only an
    address asks this first, and keeps the address as a link when there is no username.
    """
    cleaned_site = clean_token_text(site).strip()
    cleaned_name = _clean_username(name)
    if not cleaned_site:
        return FiledAt(site_id=None, username_id=None)
    async with db.read() as connection:
        row = await (
            await connection.execute(_SELECT_FILED_USERNAME, (cleaned_name, cleaned_site))
        ).fetchone()
    if row is None:
        return FiledAt(site_id=None, username_id=None)
    if not cleaned_name or row["username_id"] is None:
        return FiledAt(site_id=str(row["site_id"]), username_id=None, site=str(row["site"]))
    return FiledAt(
        site_id=str(row["site_id"]),
        username_id=str(row["username_id"]),
        site=str(row["site"]),
        username=str(row["username"]),
    )


async def ensure_site(db: Database, name: str, *, made: Made, address: str | None = None) -> str:
    """Ensure a site exists and return its id. Idempotent; matched by name (case-insensitive).

    A site login needs a site row and no username, hence apart from `seed_site_username`.
    """
    async with telling(db, EVERY_ADMIN, About.LIBRARY) as connection:
        return await _ensure_site(connection, name, made=made, address=address)


async def seed_site_username(
    db: Database,
    *,
    site: str,
    name: str,
    display_name: str | None = None,
    url: str | None = None,
    number: str | None = None,
    made: Made,
    site_address: str | None = None,
) -> tuple[str, str]:
    """Ensure a site and a username exist, and return `(site_id, username_id)`.

    Both are upserts; a later display name or URL fills a blank only. `number`, the site's own
    number for the username, outranks the name, so a renamed username stays the same row.
    """
    async with telling(db, EVERY_ADMIN, About.LIBRARY) as connection:
        return await _seed_username_on(
            connection,
            site=site,
            name=name,
            display_name=display_name,
            url=url,
            number=number,
            made=made,
            site_address=site_address,
        )


async def _seed_username_on(
    connection: Connection,
    *,
    site: str,
    name: str,
    display_name: str | None = None,
    url: str | None = None,
    number: str | None = None,
    made: Made,
    site_address: str | None = None,
    record: bool = True,
) -> tuple[str, str]:
    """The upsert itself, on a connection: the one body every caller reaches.

    A username this call makes records its arrival as an `added` event in the same transaction
    (`_record_arrival`); a second sighting is not an arrival. `record` is False only for the
    catalog's own migration, which runs before the ledger's table is certain to exist.
    """
    clean_name = _clean_username(name)
    # A display name that cleans away to nothing stays NULL, never an empty indexed string.
    clean_display = clean_stored_text(display_name).strip() or None if display_name else None
    now = int(time.time())

    # `site_address` is where the SITE lives and `url` is this username's page on it: two
    # addresses, and only the first is written onto a site being made (`_ensure_site`).
    site_id = await _ensure_site(connection, site, made=made, address=site_address)
    # The number first, where there is one. It is the only thing here that survives a rename.
    if number:
        known = await (
            await connection.execute(_SELECT_USERNAME_BY_NUMBER, (site_id, number))
        ).fetchone()
        if known is not None:
            username_id = str(known["id"])
            if str(known["name"]) != clean_name:
                # Renamed on the site: the row keeps its id and everything written on it.
                await connection.execute(
                    _RENAME_USERNAME, (clean_name, sort_key(clean_name), username_id)
                )
            return site_id, username_id
    # Asked before the upsert rather than inferred from it: an `ON CONFLICT ... DO UPDATE` counts a
    # row changed whether it inserted or filled a blank, so its own answer cannot say which.
    was_there = await (await connection.execute(_SELECT_USERNAME, (site_id, clean_name))).fetchone()
    await connection.execute(
        _INSERT_USERNAME,
        (new_id(), site_id, clean_name, sort_key(clean_name), clean_display, url, now),
    )
    username_row = await (
        await connection.execute(_SELECT_USERNAME, (site_id, clean_name))
    ).fetchone()
    username_id = str(username_row["id"])  # type: ignore[index]
    if was_there is None and record:
        await _record_arrival(
            connection, username_id=username_id, name=clean_name, site_id=site_id, made=made
        )
    if number:
        # Only ever fills a blank: a number does not change.
        await connection.execute(_SET_USERNAME_NUMBER, (number, username_id))
    return site_id, username_id


def _actor_of(made: Made) -> LedgerActor | None:
    """Who made a row, as the ledger's actor, or None where a person has no id to name. No path in
    `src` makes a username that way (`slices/workbench/tests/test_username_arrivals.py`)."""
    if made.kind == "user":
        return LedgerActor.user(made.user_id) if made.user_id else None
    return LedgerActor.sift(made.via)


async def _record_arrival(
    connection: Connection, *, username_id: str, name: str, site_id: str, made: Made
) -> None:
    """Write down that a username arrived, how, and on which Site. See `_seed_username_on`.

    The actor is the how (Sift and its pass word, or the user); a payload would repeat it."""
    actor = _actor_of(made)
    if actor is None:
        return
    await record_event(
        connection,
        actor=actor,
        verb="added",
        subject=LedgerSubject(kind="username", id=username_id, name=name),
        # Named by the ledger's door from the row: the Site is there, it was ensured a line above.
        object=LedgerObject(kind="site", id=site_id),
    )


async def link_username_to_asset(
    db: Database, *, asset_id: str, username_id: str, source: str | None = None
) -> None:
    """Attach a username to an asset. Idempotent. `source` None is a person; a download names
    itself (`VIA_DOWNLOAD`). See `_LINK_ASSET_USERNAME`."""
    async with telling(db, EVERY_ADMIN, About.LIBRARY) as connection:
        await connection.execute(
            _LINK_ASSET_USERNAME, (asset_id, username_id, source, int(time.time()), None)
        )


#: The name on the row that means "from this site, poster unknown". Empty rather than a word,
#: because it is the absence of a name; `UNIQUE (site_id, name)` gives one such row per site.
UNATTRIBUTED = ""


async def link_asset_to_site(
    db: Database,
    *,
    asset_id: str,
    site: str,
    source: str | None = None,
    made: Made,
    site_address: str | None = None,
    box_id: str | None = None,
) -> str:
    """File an asset under a site when nothing names who posted it. Returns the site id.

    Every question about a site reaches a file through `asset_usernames`, so the file goes under
    the site's nameless row (`file_assets_under_site_on`) rather than being findable by nothing.
    """
    async with telling(db, EVERY_ADMIN, About.LIBRARY) as connection:
        return await file_assets_under_site_on(
            connection,
            asset_ids=[asset_id],
            site=site,
            source=source,
            made=made,
            site_address=site_address,
            box_id=box_id,
        )


#: A username on a site by its name without case: the row already held is found first and its
#: spelling kept, rather than a second row made beside it.
_SELECT_USERNAME_FOLDED = (
    "SELECT name FROM usernames WHERE site_id = ? AND name = ? COLLATE NOCASE ORDER BY id LIMIT 1"
)


async def file_asset_under_username(
    db: Database,
    *,
    asset_id: str,
    site: str,
    name: str,
    url: str | None = None,
    source: str | None = None,
    made: Made,
    person_id: str | None = None,
    box_id: str | None = None,
) -> bool:
    """File an asset under a NAMED username on a site, in one transaction. True when newly filed.

    A filing that knows the poster never lands on the nameless row. The username is found without
    case, then upserted through `_seed_username_on`; `person_id` fills a blank only.
    """
    clean = _clean_username(name)
    async with telling(db, EVERY_ADMIN, About.LIBRARY) as connection:
        site_id = await _ensure_site(connection, site, made=made, address=url)
        held = await (
            await connection.execute(_SELECT_USERNAME_FOLDED, (site_id, clean))
        ).fetchone()
        _, username_id = await _seed_username_on(
            connection,
            site=site,
            name=str(held["name"]) if held is not None else clean,
            url=url or None,
            made=made,
            site_address=url,
        )
        filed = await link_username_to_asset_on(
            connection, asset_id=asset_id, username_id=username_id, source=source, box_id=box_id
        )
        if person_id:
            await link_username_to_person(connection, username_id=username_id, person_id=person_id)
    return filed


async def seed_site_username_on(
    connection: Connection,
    *,
    site: str,
    name: str,
    number: str | None = None,
    made: Made,
) -> tuple[str, str]:
    """The site-and-username upsert on a caller's connection. Returns `(site_id, id)`."""
    return await _seed_username_on(
        connection,
        site=site,
        name=name,
        number=number,
        made=made,
    )


async def file_assets_under_site_on(
    connection: Connection,
    *,
    asset_ids: Sequence[str],
    site: str,
    source: str | None = None,
    made: Made,
    site_address: str | None = None,
    landed: list[tuple[str, str]] | None = None,
    box_id: str | None = None,
) -> str:
    """File these assets under a site with nobody named as the poster. Returns the site id.

    `landed`, when given, collects the filings this call actually wrote, so an undo takes back only
    those. `source` None is a person; the insert keeps the first answer. `box_id` is the stash-box
    whose answer decided it, where `source` is a box's.
    """
    now = int(time.time())
    # `made` (who invented the site) and `source` (how the filing was decided) stay apart: a person
    # dropping a file on a new name writes no source and still made the site. No arrival is
    # recorded for the nameless row: what arrives is the filing, which the caller records.
    site_id = await _ensure_site(connection, site, made=made, address=site_address)
    await connection.execute(
        _INSERT_USERNAME,
        (new_id(), site_id, UNATTRIBUTED, sort_key(UNATTRIBUTED), None, None, now),
    )
    row = await (await connection.execute(_SELECT_USERNAME, (site_id, UNATTRIBUTED))).fetchone()
    username_id = str(row["id"])  # type: ignore[index]
    for asset_id in asset_ids:
        cursor = await connection.execute(
            _LINK_ASSET_USERNAME, (asset_id, username_id, source, now, box_id)
        )
        if landed is not None and cursor.rowcount > 0:
            landed.append((asset_id, username_id))
    return site_id


async def link_username_to_asset_on(
    connection: Connection,
    *,
    asset_id: str,
    username_id: str,
    source: str | None = None,
    box_id: str | None = None,
) -> bool:
    """File one asset under one username, on a caller's connection. False where already filed.

    The answer is whether a row was written, so a pass records only its own filings and an undo
    never removes one somebody made (`DO NOTHING` keeps the first answer).
    """
    cursor = await connection.execute(
        _LINK_ASSET_USERNAME, (asset_id, username_id, source, int(time.time()), box_id)
    )
    return bool(cursor.rowcount)
