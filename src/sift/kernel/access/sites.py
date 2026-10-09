# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a site reaches, written once for everything that has to agree about it."""

from __future__ import annotations

import json
import time
from collections.abc import Sequence

from sift.kernel.db import Connection

#: Every site each site reaches, itself included. UNION, so a parent loop ends.
SITE_REACH = (
    "WITH RECURSIVE reach_up(site_id, ancestor_id) AS ("
    "SELECT id, id FROM sites"
    " UNION SELECT r.site_id, p.parent_id FROM reach_up r"
    " JOIN sites p ON p.id = r.ancestor_id WHERE p.parent_id IS NOT NULL)"
    " SELECT site_id, ancestor_id FROM reach_up"
)

#: The files a set of named sites reaches; `{ancestors}` is filled with constants only.
FILES_SITES_REACH = (
    "SELECT aa.asset_id FROM asset_usernames aa"  # noqa: S608
    " JOIN usernames ac ON ac.id = aa.username_id"
    " JOIN (" + SITE_REACH + ") reach ON reach.site_id = ac.site_id"
    " WHERE reach.ancestor_id {ancestors}"
)

#: Whether one site is within another's reach, asked before a parent write makes a loop.
SITE_WITHIN = (
    "SELECT 1 FROM (" + SITE_REACH + ") reach"  # noqa: S608
    " WHERE reach.site_id = ? AND reach.ancestor_id = ? LIMIT 1"
)

#: Whether a site is concealed from this viewer: itself or anything above it hidden.
SITE_CONCEALED = (
    "EXISTS (SELECT 1 FROM (" + SITE_REACH + ") reach"  # noqa: S608
    " JOIN site_user_state hl ON hl.site_id = reach.ancestor_id"
    " WHERE reach.site_id = {site} AND hl.user_id = :viewer AND hl.hidden = 1)"
)


#: A Site's address: the first of its links by id, a scalar subquery so it never multiplies a row.
SITE_ADDRESS = "(SELECT sa.url FROM site_links sa WHERE sa.site_id = {site} ORDER BY sa.id LIMIT 1)"


def site_address(alias: str) -> str:
    """`SITE_ADDRESS` over the Site a statement aliases `alias`."""
    return SITE_ADDRESS.format(site=f"{alias}.id")


# --- Which Site a known site files under, remembered per key so a rename makes no second Site.

_HAS_KEYS = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'download_sites'"

_SITE_FOR_KEY = (
    "SELECT s.id AS id, s.name AS name FROM download_sites k JOIN sites s ON s.id = k.site_id"
    " WHERE k.key = ?"
)
_SITES_FOR_KEYS = (
    "SELECT k.key AS key, s.name AS name FROM download_sites k JOIN sites s ON s.id = k.site_id"
    " WHERE k.key IN (SELECT value FROM json_each(?))"
)
#: The first answer stands until that Site is deleted.
_KEEP_SITE_KEY = (
    "INSERT INTO download_sites (key, site_id, filed_at) VALUES (?, ?, ?)"
    " ON CONFLICT(key) DO NOTHING"
)


def host_key(host: str) -> str | None:
    """The key of a site known only by its address: `@` and the host without `www.`."""
    bare = host.strip().lower().removeprefix("www.")
    return f"@{bare}" if bare else None


async def _keys_kept(connection: Connection) -> bool:
    return bool(await connection.execute_fetchall(_HAS_KEYS))


async def site_for_key_on(connection: Connection, key: str) -> tuple[str, str] | None:
    """The Site the site `key` files under, as its id and name now, or None."""
    if not await _keys_kept(connection):
        return None
    rows = list(await connection.execute_fetchall(_SITE_FOR_KEY, (key,)))
    return (str(rows[0]["id"]), str(rows[0]["name"])) if rows else None


async def site_names_for_keys_on(connection: Connection, keys: Sequence[str]) -> dict[str, str]:
    """`site_for_key_on` for a page of keys."""
    if not keys or not await _keys_kept(connection):
        return {}
    rows = await connection.execute_fetchall(_SITES_FOR_KEYS, (json.dumps(list(keys)),))
    return {str(row["key"]): str(row["name"]) for row in rows}


async def keep_site_key_on(connection: Connection, key: str, site_id: str) -> None:
    """Remember that the site `key` files under `site_id`, unless it already files somewhere."""
    if await _keys_kept(connection):
        await connection.execute(_KEEP_SITE_KEY, (key, site_id, int(time.time())))
