# SPDX-License-Identifier: AGPL-3.0-or-later
"""Carrying an attribution from a file onto its copies, and taking it back."""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass

from sift.kernel.access.catalog.people import _LINK_ASSET_PERSON
from sift.kernel.access.catalog.usernames import _LINK_ASSET_USERNAME
from sift.kernel.db import Connection, Database

# Copies of the same bytes are one asset already. This carries onto a file that merely looks the
# same (a re-encode, a second download), as an offer somebody presses on a group, and only where
# the group's attributed files agree: a fingerprint match is not proof of the same content.

#: What a carried row says it is in `source`: its own word, because a copy's claim is only as good
#: as the match, so a screen can mark it and an undo find exactly what a carry wrote.
COPIED = "copy"


@dataclass(frozen=True, slots=True)
class Carried:
    """One attribution, with the name a sentence elsewhere says it by.

    `site_id` and `site` are None on a person and on a username whose site has been deleted.
    """

    #: `person` or `username`, the ledger's own kinds; a tag is never carried.
    kind: str
    #: The person, or the username. Whatever that kind's link table points at.
    id: str
    #: The person's name, or the username: the words the sentence uses.
    name: str
    site_id: str | None = None
    site: str | None = None


#: Who is on these files and where they are filed: two tables, two reads, a page of files at a time
#: through one `IN` rather than two statements per file.
_PEOPLE_ON = """
SELECT link.asset_id AS asset_id, p.id AS id, p.name AS name
  FROM asset_people link
  JOIN people p ON p.id = link.person_id
 WHERE link.asset_id IN (SELECT value FROM json_each(?))
 ORDER BY link.asset_id, p.id
"""

_USERNAMES_ON = """
SELECT link.asset_id AS asset_id, ac.id AS id, ac.name AS name,
       pl.id AS site_id, pl.name AS site
  FROM asset_usernames link
  JOIN usernames ac ON ac.id = link.username_id
  LEFT JOIN sites pl ON pl.id = ac.site_id
 WHERE link.asset_id IN (SELECT value FROM json_each(?))
 ORDER BY link.asset_id, ac.id
"""


#: Taking back exactly what a carry wrote: `source = 'copy'` in the WHERE leaves a row somebody
#: else's decision wrote for the same person.
_UNCARRY_PERSON = "DELETE FROM asset_people WHERE asset_id = ? AND person_id = ? AND source = ?"
_UNCARRY_USERNAME = (
    "DELETE FROM asset_usernames WHERE asset_id = ? AND username_id = ? AND source = ?"
)


async def attribution_of_files(
    database: Database, asset_ids: Sequence[str]
) -> dict[str, list[Carried]]:
    """Who is named in each of these files and where each is filed, in a sentence's words.

    Every file asked is a key. People then filings, each in id order, so agreeing copies compare
    equal."""
    found: dict[str, list[Carried]] = {str(one): [] for one in asset_ids}
    if not found:
        return found
    wanted = sorted(found)
    # One JSON list binds any number of files, so the whole set is two statements.
    bound = (json.dumps(wanted),)
    for row in await database.fetch_all(_PEOPLE_ON, bound):
        found[str(row["asset_id"])].append(
            Carried(kind="person", id=str(row["id"]), name=str(row["name"]))
        )
    for row in await database.fetch_all(_USERNAMES_ON, bound):
        found[str(row["asset_id"])].append(
            Carried(
                kind="username",
                id=str(row["id"]),
                name=str(row["name"]),
                site_id=None if row["site_id"] is None else str(row["site_id"]),
                site=None if row["site"] is None else str(row["site"]),
            )
        )
    return found


async def carry_attribution_on(
    connection: Connection, *, carried: Sequence[Carried], to_asset_ids: Sequence[str]
) -> dict[str, list[Carried]]:
    """Write these attributions onto these files as copies. What actually landed, per file.

    Which rows landed, never how many, so an undo detaches nothing an earlier decision wrote; the
    insert keeps the first answer. Tags are not carried: a person or a site is where a file came
    from, which copies share, while a tag judges the one file in front of somebody.
    """
    decided_at = int(time.time())
    landed: dict[str, list[Carried]] = {}
    for asset_id in to_asset_ids:
        wrote: list[Carried] = []
        for one in carried:
            if one.kind == "person":
                cursor = await connection.execute(
                    _LINK_ASSET_PERSON, (asset_id, one.id, COPIED, decided_at, None)
                )
            else:
                cursor = await connection.execute(
                    _LINK_ASSET_USERNAME, (asset_id, one.id, COPIED, decided_at, None)
                )
            if cursor.rowcount:
                wrote.append(one)
        if wrote:
            landed[asset_id] = wrote
    return landed


async def uncarry_attribution_on(
    connection: Connection, *, asset_id: str, carried: Sequence[Carried]
) -> int:
    """Take back the rows one carry wrote onto one file, the grain its undo is offered at."""
    removed = 0
    for one in carried:
        statement = _UNCARRY_PERSON if one.kind == "person" else _UNCARRY_USERNAME
        cursor = await connection.execute(statement, (asset_id, one.id, COPIED))
        removed += int(cursor.rowcount or 0)
    return removed
