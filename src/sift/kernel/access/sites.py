# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a site reaches, written once, for everything that has to agree about it.

A site can be part of another (`sites.parent_id`), and a file is filed under the label that
released it, never the network. So the files a site reaches are those of its usernames taken over
the site AND every site whose parent chain arrives at it, and the search leaf, the wall count, the
sharing and concealment memberships, the trigger on `parent_id` and the name rules must all give
that one answer: they read `SITE_REACH`, `FILES_SITES_REACH` and `SITE_CONCEALED`.

The walk goes UP from every site, as `folder_ancestry` does, because concealment starts from a file
and has no names to seed a downward walk; sites number in the hundreds, so it costs a step per
level. UNION, not UNION ALL, so a parent loop in a hand-edited database terminates. Each is a whole
SELECT so it drops into a FROM, IN, EXISTS or trigger body that already owns a `WITH RECURSIVE`;
SQLite evaluates the recursive arm once and indexes the result, so a correlated use is a probe per
row, not a walk.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence

from sift.kernel.db import Connection

# The migration steps in `kernel/access/schema.py` and the slices' `schema.py` keep the object's old
# word, and must: a step describes what a database WAS.

#: Every site each site reaches: itself, and everything whose parent chain arrives at it. Two
#: columns so both directions can be asked: bind `site_id` for who reaches a file, `ancestor_id`
#: for what a site reaches. `reach_up` is named not to collide with the host statement's CTEs.
SITE_REACH = (
    "WITH RECURSIVE reach_up(site_id, ancestor_id) AS ("
    "SELECT id, id FROM sites"
    " UNION SELECT r.site_id, p.parent_id FROM reach_up r"
    " JOIN sites p ON p.id = r.ancestor_id WHERE p.parent_id IS NOT NULL)"
    " SELECT site_id, ancestor_id FROM reach_up"
)

#: The files a set of named sites reaches, as a subquery over `asset_usernames`, shared by the
#: search leaf and the trigger. `{ancestors}` is filled by the caller with a `json_each` group or a
#: trigger's `NEW`, never with a value arriving at run time.
# `noqa: S608`: only module constants are concatenated or filled in; values bind as parameters.
FILES_SITES_REACH = (
    "SELECT aa.asset_id FROM asset_usernames aa"  # noqa: S608
    " JOIN usernames ac ON ac.id = aa.username_id"
    " JOIN (" + SITE_REACH + ") reach ON reach.site_id = ac.site_id"
    " WHERE reach.ancestor_id {ancestors}"
)

#: Whether one site is within another's reach: bind the site, then the ancestor; one row when it
#: is. What a parent write asks before it makes a loop.
# `noqa: S608`: the only thing concatenated in is `SITE_REACH`, a constant in this file.
SITE_WITHIN = (
    "SELECT 1 FROM (" + SITE_REACH + ") reach"  # noqa: S608
    " WHERE reach.site_id = ? AND reach.ancestor_id = ? LIMIT 1"
)

#: Whether a site is concealed from this viewer: itself or anything above it hidden. Hiding a
#: network takes its labels' names, counts and pages off the Sites wall too, since a listed name is
#: the disclosure. A site is its own ancestor, so this replaces the plain `hidden` test rather than
#: standing beside it. `{site}` is a column name, never a run-time value; `:viewer` binds.
# `noqa: S608`: the only thing concatenated in is `SITE_REACH`, a constant in this file.
SITE_CONCEALED = (
    "EXISTS (SELECT 1 FROM (" + SITE_REACH + ") reach"  # noqa: S608
    " JOIN site_user_state hl ON hl.site_id = reach.ancestor_id"
    " WHERE reach.site_id = {site} AND hl.user_id = :viewer AND hl.hidden = 1)"
)


#: A Site's address: the first of its links by id, a ULID minted in write order (a clock that
#: steps backwards cannot reorder it), so the logo, the wall, the record and search all read the
#: same address. A scalar subquery so it never multiplies a row; `{site}` is a column name, never a
#: run-time value; `ix_site_links_site` answers it.
SITE_ADDRESS = "(SELECT sa.url FROM site_links sa WHERE sa.site_id = {site} ORDER BY sa.id LIMIT 1)"


def site_address(alias: str) -> str:
    """`SITE_ADDRESS` over the Site a statement aliases `alias`: `site_address("s")` for `s.id`.

    The one place the fragment is filled: an alias is written in the statement, never a run-time
    value, and readers splice the result under `{{SITE_ADDRESS}}`.
    """
    return SITE_ADDRESS.format(site=f"{alias}.id")


# --- Which Site a known site files under ---------------------------------------------------------
#
# A site known by its address files under ONE Site here, whose NAME is somebody's to change, so
# the library remembers the Site per site key (`download_sites`: the catalog's key, or `host_key`)
# and passes find it through the key; a lookup by name would make a second Site after a rename.
# The table is the download slice's, so a database built for another slice's test answers nothing.

#: Whether the remembered keys are there to read.
_HAS_KEYS = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'download_sites'"

#: The Site a key files under, with the name it goes by now.
_SITE_FOR_KEY = (
    "SELECT s.id AS id, s.name AS name FROM download_sites k JOIN sites s ON s.id = k.site_id"
    " WHERE k.key = ?"
)
#: The same for a page of keys at once, for a list that draws many rows.
_SITES_FOR_KEYS = (
    "SELECT k.key AS key, s.name AS name FROM download_sites k JOIN sites s ON s.id = k.site_id"
    " WHERE k.key IN (SELECT value FROM json_each(?))"
)
#: Which Site a site files under, from the first filing on: the first answer stands until that
#: Site is deleted, which takes the key with it.
_KEEP_SITE_KEY = (
    "INSERT INTO download_sites (key, site_id, filed_at) VALUES (?, ?, ?)"
    " ON CONFLICT(key) DO NOTHING"
)


def host_key(host: str) -> str | None:
    """The key of a site known only by its address: the host without a leading `www.`, marked.

    `@example.com`: the mark keeps it apart from a catalog key, and it has no colon because a
    creator picture's scope `<key>:<username>` is cut at its first colon. None for an empty host.
    """
    bare = host.strip().lower().removeprefix("www.")
    return f"@{bare}" if bare else None


async def _keys_kept(connection: Connection) -> bool:
    return bool(await connection.execute_fetchall(_HAS_KEYS))


async def site_for_key_on(connection: Connection, key: str) -> tuple[str, str] | None:
    """The Site this library files the site `key` names under, as its id and its name now.

    None where nothing has been filed from that site yet, or the Site it filed under was deleted.
    """
    if not await _keys_kept(connection):
        return None
    rows = list(await connection.execute_fetchall(_SITE_FOR_KEY, (key,)))
    return (str(rows[0]["id"]), str(rows[0]["name"])) if rows else None


async def site_names_for_keys_on(connection: Connection, keys: Sequence[str]) -> dict[str, str]:
    """`site_for_key_on` for a page of keys at once: each key that files somewhere, to its name."""
    if not keys or not await _keys_kept(connection):
        return {}
    rows = await connection.execute_fetchall(_SITES_FOR_KEYS, (json.dumps(list(keys)),))
    return {str(row["key"]): str(row["name"]) for row in rows}


async def keep_site_key_on(connection: Connection, key: str, site_id: str) -> None:
    """Remember that the site `key` names files under `site_id`, unless it already files somewhere.

    Not announced: nothing on a screen draws it. It is what `site_for_key_on` reads to find the
    Site again after somebody renames it.
    """
    if await _keys_kept(connection):
        await connection.execute(_KEEP_SITE_KEY, (key, site_id, int(time.time())))
