# SPDX-License-Identifier: AGPL-3.0-or-later
"""People on files: the person a username names, attributing and detaching, aliases, refusals.

Most writes here take the caller's connection, so a decision made of several writes lands whole.
"""

from __future__ import annotations

import time
from collections.abc import Sequence

from sift.kernel.access.catalog.made import Made
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.db import Connection, Database
from sift.kernel.ids import new_id
from sift.kernel.sorting import sort_key
from sift.kernel.text import clean_stored_text, clean_token_text

# Everybody a word names, by name or alias, NOCASE as the tables are. Every match comes back: two
# people may answer to one word, and what to do about that is the caller's business.
_PEOPLE_NAMED = """
SELECT id FROM people WHERE name = :term COLLATE NOCASE
UNION
SELECT person_id FROM people_aliases WHERE alias = :term COLLATE NOCASE
"""

#: Every caller hands a pass, never a person (the People screen has its own statement), and a row
#: that turns out to be a box's is corrected by `mark_created_by_box`.
_INSERT_PERSON = (
    "INSERT INTO people"
    " (id, name, name_sort, created_at, created_by_kind, created_by_via, created_by_user_id)"
    " VALUES (?, ?, ?, ?, ?, ?, ?)"
)

#: Give a person a picture only where they have neither kind (`kernel/covers.py` says which wins).
#: The moment and face pointers stay NULL: filling a gap has nothing of an old cover to clear,
#: unlike the people slice's `_SET_PERSON_COVER`.
_GIVE_PERSON_A_COVER = (
    "UPDATE people SET cover_asset_id = ? "
    "WHERE id = ? AND cover_asset_id IS NULL AND cover_upload_id IS NULL"
)

#: Somebody said this person is not in this file. Read by anything that would put them back.
_REFUSE_PERSON = (
    "INSERT INTO asset_person_refusals (asset_id, person_id, refused_at) VALUES (?, ?, ?) "
    "ON CONFLICT(asset_id, person_id) DO UPDATE SET refused_at = excluded.refused_at"
)

_UNREFUSE_PERSON = "DELETE FROM asset_person_refusals WHERE asset_id = ? AND person_id = ?"

_REFUSED_FOR_PERSON = "SELECT asset_id FROM asset_person_refusals WHERE person_id = ?"

#: `decided_at` for the same reason the filing above carries one, and with the same `DO NOTHING`
#: rule: the first answer stands, so agreeing again later does not move the date.
_LINK_ASSET_PERSON = (
    "INSERT INTO asset_people (asset_id, person_id, source, decided_at, box_id)"
    " VALUES (?, ?, ?, ?, ?) "
    "ON CONFLICT(asset_id, person_id) DO NOTHING"
)


#: An alias, and the link from a username to the person behind it: the same upserts the People
#: screens make, so the two cannot disagree about what counts as a duplicate.
_INSERT_ALIAS = (
    "INSERT INTO people_aliases (id, person_id, alias, alias_sort, added_at)"
    " VALUES (?, ?, ?, ?, ?) "
    "ON CONFLICT(person_id, alias COLLATE NOCASE) DO NOTHING"
)

#: Only where the username has nobody yet: a People screen's answer outranks a folder name.
_LINK_USERNAME_PERSON = "UPDATE usernames SET person_id = ? WHERE id = ? AND person_id IS NULL"

#: Taking those back for an undone decision, each filtered to the person the decision named, so a
#: link made since to somebody else is left alone.
_UNLINK_ASSET_PERSON = "DELETE FROM asset_people WHERE asset_id = ? AND person_id = ?"
_REMOVE_ALIAS = "DELETE FROM people_aliases WHERE person_id = ? AND alias = ? COLLATE NOCASE"
_UNLINK_USERNAME_PERSON = "UPDATE usernames SET person_id = NULL WHERE id = ? AND person_id = ?"
_REMOVE_PERSON = "DELETE FROM people WHERE id = ?"

#: Whether anything at all is attached to a person, asked one table at a time.
_PERSON_HAS_FILES = "SELECT COUNT(*) AS total FROM asset_people WHERE person_id = ?"
_PERSON_HAS_ALIASES = "SELECT COUNT(*) AS total FROM people_aliases WHERE person_id = ?"
_PERSON_HAS_USERNAMES = "SELECT COUNT(*) AS total FROM usernames WHERE person_id = ?"


async def people_named(db: Database, term: str) -> list[str]:
    """Everybody this word names, by their name or by an alias. May be empty, one, or several."""
    cleaned = clean_token_text(term).strip()
    if not cleaned:
        return []
    rows = await db.fetch_all(_PEOPLE_NAMED, {"term": cleaned})
    return [str(row["id"]) for row in rows]


async def attribute_to_person(
    db: Database,
    *,
    asset_id: str,
    name: str,
    username_id: str | None = None,
    create_if_unknown: bool = False,
    made: Made,
) -> str | None:
    """Put a downloaded file under the person its username names. Returns who, or None.

    Exactly one match, or nothing happens: two people may answer to one word, and a guess would
    file somebody's video under a stranger unseen. `create_if_unknown` makes the person when nothing
    matched, for a site whose usernames name people (the caller knows that). Idempotent.
    """
    found = await people_named(db, name)
    if len(found) > 1:
        return None
    if not found and not create_if_unknown:
        return None

    if found:
        person_id = found[0]
    else:
        person_id = new_id()
        # As the site writes it, cleaned: an invented capital matches nothing anybody types.
        person_name = clean_stored_text(name).strip()
        if not person_name:
            return None

    # One write for the person, the file and the username, told once.
    async with telling(db, EVERY_ADMIN, About.LIBRARY) as connection:
        if not found:
            await connection.execute(
                _INSERT_PERSON,
                (
                    person_id,
                    person_name,
                    sort_key(person_name),
                    int(time.time()),
                    made.kind,
                    made.via,
                    made.user_id,
                ),
            )
        await connection.execute(
            _LINK_ASSET_PERSON, (asset_id, person_id, None, int(time.time()), None)
        )
        # And the username is that person too, or the queue of usernames waiting keeps asking.
        # Nothing is guessed: one person answers to it, or the person was just made from it.
        if username_id is not None:
            await connection.execute(_LINK_USERNAME_PERSON, (person_id, username_id))
    return person_id


async def people_named_on(connection: Connection, term: str) -> list[str]:
    """Everybody this word names, asked on a connection somebody else opened."""
    cleaned = clean_token_text(term).strip()
    if not cleaned:
        return []
    rows = await (await connection.execute(_PEOPLE_NAMED, {"term": cleaned})).fetchall()
    return [str(row["id"]) for row in rows]


async def create_person_on(connection: Connection, name: str, *, made: Made) -> str | None:
    """Make a person with this name, exactly as written. None when the name cleans away to nothing."""
    cleaned = clean_stored_text(name).strip()
    if not cleaned:
        return None
    person_id = new_id()
    await connection.execute(
        _INSERT_PERSON,
        (
            person_id,
            cleaned,
            sort_key(cleaned),
            int(time.time()),
            made.kind,
            made.via,
            made.user_id,
        ),
    )
    return person_id


async def give_person_a_cover_on(connection: Connection, *, person_id: str, asset_id: str) -> bool:
    """Make a file this person's picture, but only where they have none. True when one landed.

    The condition is in the statement, not a read then a write with a gap for another writer.
    """
    if not person_id or not asset_id:
        return False
    cursor = await connection.execute(_GIVE_PERSON_A_COVER, (asset_id, person_id))
    return bool(cursor.rowcount)


async def attribute_assets_on(
    connection: Connection,
    *,
    asset_ids: Sequence[str],
    person_id: str,
    source: str | None = None,
) -> int:
    """Put a person on every one of these files. Returns how many rows were new.

    `source` None means a person decided; a pass names itself. The first answer wins, so a hand
    attribution stays one when a later pass agrees, and a repeat is a no-op.
    """
    return len(
        await attribute_assets_recording_on(
            connection, asset_ids=asset_ids, person_id=person_id, source=source
        )
    )


async def attribute_assets_recording_on(
    connection: Connection,
    *,
    asset_ids: Sequence[str],
    person_id: str,
    source: str | None = None,
    box_id: str | None = None,
) -> list[str]:
    """The same write, returning WHICH files it put the person on rather than how many.

    That is what lets an undo detach only those, never files an earlier decision covered.
    `box_id` is the stash-box whose answer decided it, where `source` is a box's.
    """
    # One stamp for the whole decision, so a history draws one act rather than forty.
    decided_at = int(time.time())
    written: list[str] = []
    for asset_id in asset_ids:
        cursor = await connection.execute(
            _LINK_ASSET_PERSON, (asset_id, person_id, source, decided_at, box_id)
        )
        if cursor.rowcount:
            written.append(asset_id)
    return written


async def detach_person_on(
    connection: Connection, *, asset_ids: Sequence[str], person_id: str
) -> int:
    """Take a person back off these files. Returns how many rows went.

    No refusal is recorded: an undone decision says nothing about the file, and a refusal would
    stop a later pass from attributing it correctly.
    """
    removed = 0
    for asset_id in asset_ids:
        cursor = await connection.execute(_UNLINK_ASSET_PERSON, (asset_id, person_id))
        removed += int(cursor.rowcount or 0)
    return removed


async def remove_alias_on(connection: Connection, *, person_id: str, alias: str) -> bool:
    """Take back a spelling this person answers to. False when there was nothing to remove."""
    cleaned = clean_token_text(alias).strip()
    if not cleaned:
        return False
    cursor = await connection.execute(_REMOVE_ALIAS, (person_id, cleaned))
    return bool(cursor.rowcount)


async def unlink_username_from_person_on(
    connection: Connection, *, username_id: str, person_id: str
) -> bool:
    """Take the person back off a username. False when it names somebody else now."""
    cursor = await connection.execute(_UNLINK_USERNAME_PERSON, (username_id, person_id))
    return bool(cursor.rowcount)


async def person_is_bare_on(connection: Connection, person_id: str) -> bool:
    """Whether this person now has nothing at all attached to them.

    Asked before an undo removes a person it created: files or usernames put on them since would
    otherwise go with them.
    """
    for statement in (_PERSON_HAS_FILES, _PERSON_HAS_ALIASES, _PERSON_HAS_USERNAMES):
        row = await (await connection.execute(statement, (person_id,))).fetchone()
        if row is not None and int(row["total"]):
            return False
    return True


async def remove_person_on(connection: Connection, person_id: str) -> bool:
    """Remove a person. Only ever called for one an undo has just emptied. See above."""
    cursor = await connection.execute(_REMOVE_PERSON, (person_id,))
    return bool(cursor.rowcount)


async def refuse_person_on(
    connection: Connection, *, asset_id: str, person_id: str, now: int
) -> None:
    """Remember that somebody took this person off this file, whoever had put them there."""
    await connection.execute(_REFUSE_PERSON, (asset_id, person_id, now))


async def clear_refusal_on(connection: Connection, *, asset_id: str, person_id: str) -> None:
    """Forget a refusal, because somebody has just attributed the same person again."""
    await connection.execute(_UNREFUSE_PERSON, (asset_id, person_id))


async def refused_for(database: Database, person_id: str, asset_ids: Sequence[str]) -> set[str]:
    """Of these files, the ones somebody has taken this person off: one read for the person."""
    if not asset_ids:
        return set()
    rows = await database.fetch_all(_REFUSED_FOR_PERSON, (person_id,))
    refused = {str(row["asset_id"]) for row in rows}
    return refused & set(asset_ids)


async def add_alias_on(connection: Connection, *, person_id: str, alias: str) -> bool:
    """Record a spelling this person answers to. False when there was nothing to add.

    An alias equal to the person's own name is still written: the unique key is per person.
    """
    cleaned = clean_token_text(alias).strip()
    if not cleaned:
        return False
    cursor = await connection.execute(
        _INSERT_ALIAS, (new_id(), person_id, cleaned, sort_key(cleaned), int(time.time()))
    )
    return bool(cursor.rowcount)


async def link_username_to_person(
    connection: Connection, *, username_id: str, person_id: str
) -> bool:
    """Say that this username on this site is this person. False when it already named somebody."""
    cursor = await connection.execute(_LINK_USERNAME_PERSON, (person_id, username_id))
    return bool(cursor.rowcount)
