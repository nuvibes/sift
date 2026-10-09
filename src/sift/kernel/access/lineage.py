# SPDX-License-Identifier: AGPL-3.0-or-later
"""Giving a file Sift produced what was recorded about its source: what conceals or describes it
follows the copy, what exposes it to somebody else does not."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sift.kernel.access.stamps import bump_stamps_for_object
from sift.kernel.access.viewer import ObjectType
from sift.kernel.audience import NOBODY, Audience
from sift.kernel.changes import About, announce, telling
from sift.kernel.db import Database
from sift.kernel.log import get_logger
from sift.kernel.sorting import sort_key
from sift.kernel.vocabulary import MADE_ACTS

if TYPE_CHECKING:  # pragma: no cover - the import exists for the signature, not for the call
    from sift.kernel.access.repository import Repository

log = get_logger(__name__)

# The source and `decided_at` come across: a blank source would read as a person's choice, and
# today's date as a new decision.
_COPY_TAGS = """
INSERT INTO asset_tags (asset_id, tag_id, source, decided_at)
SELECT ?, tag_id, source, decided_at FROM asset_tags WHERE asset_id = ?
ON CONFLICT(asset_id, tag_id) DO NOTHING
"""

_TAGS_OF = "SELECT tag_id FROM asset_tags WHERE asset_id = ?"

# The source and the moment come across, as for the tag above.
_COPY_PEOPLE = """
INSERT INTO asset_people (asset_id, person_id, source, decided_at)
SELECT ?, person_id, source, decided_at FROM asset_people WHERE asset_id = ?
ON CONFLICT(asset_id, person_id) DO NOTHING
"""

_PEOPLE_OF = "SELECT person_id FROM asset_people WHERE asset_id = ?"

# A copy joins each collection its source is in, `added_at` carried. A photo set is a folder's own
# pictures, so it is not carried.
_COPY_COLLECTIONS = """
INSERT INTO collection_items (collection_id, asset_id, added_at)
SELECT held.collection_id, ?, held.added_at
  FROM collection_items held
 WHERE held.asset_id = ?
ON CONFLICT(collection_id, asset_id) DO NOTHING
"""

_COLLECTIONS_OF = "SELECT collection_id FROM collection_items WHERE asset_id = ?"

# Only the columns that describe or conceal the content; `hidden_at` orders the Hidden screen.
_CARRIED_STATE = """
SELECT user_id, rating, hidden, hidden_at
  FROM asset_user_state
 WHERE asset_id = ? AND (rating IS NOT NULL OR hidden = 1)
"""

_WRITE_STATE = """
INSERT INTO asset_user_state (asset_id, user_id, rating, hidden, hidden_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(asset_id, user_id) DO UPDATE SET
  rating = excluded.rating,
  hidden = excluded.hidden,
  hidden_at = excluded.hidden_at,
  updated_at = excluded.updated_at
"""


_TAG_BY_NAME = "SELECT id FROM tags WHERE name = ?"

#: The tag a copy wears to say Sift made it, by the `produced` pass; the act is bound
#: (`vocabulary.MADE_ACTS`).
_INSERT_TAG = """
INSERT INTO tags (id, name, name_sort, created_at, created_by_kind, created_by_via, created_by_act)
VALUES (?, ?, ?, ?, 'sift', 'produced', ?)
ON CONFLICT(name) DO NOTHING
"""

_ATTACH_TAG = """
INSERT INTO asset_tags (asset_id, tag_id, source, decided_at) VALUES (?, ?, 'produced', ?)
ON CONFLICT(asset_id, tag_id) DO NOTHING
"""


async def tag_produced(
    database: Database, *, asset_id: str, tag_name: str, act: str, now: int, new_id: str
) -> str:
    """Put an ordinary tag on a file Sift produced, making it by name if it is not there."""
    if act not in MADE_ACTS:
        raise ValueError(f"no act is called {act!r}")
    async with database.write() as connection:
        await connection.execute(_INSERT_TAG, (new_id, tag_name, sort_key(tag_name), now, act))
        rows = list(await connection.execute_fetchall(_TAG_BY_NAME, (tag_name,)))
        tag_id = str(rows[0]["id"])
        await connection.execute(_ATTACH_TAG, (asset_id, tag_id, now))
        # A tag is what a share or a hide hangs off, so joining one changes what some users may see.
        announce(await bump_stamps_for_object(connection, ObjectType.TAG, tag_id), About.LIBRARY)
    return tag_id


_USERS = "SELECT id FROM users WHERE disabled = 0"

_CONCEAL = """
INSERT INTO asset_user_state (asset_id, user_id, hidden, hidden_at, updated_at)
VALUES (?, ?, 1, ?, ?)
ON CONFLICT(asset_id, user_id) DO UPDATE SET
  hidden = 1, hidden_at = excluded.hidden_at, updated_at = excluded.updated_at
"""


@dataclass(frozen=True, slots=True)
class Inherited:
    """What was carried across, for a log and a test."""

    tags: int
    people: int
    #: How many users the copy was hidden from because the original's own row said so.
    hidden_for: int
    ratings: int
    collections: int = 0
    #: How many users it was concealed from on top of that, because something it does not inherit
    #: concealed the original.
    concealed_for: int = 0


async def _match_concealment(
    database: Database, access: Repository, *, source_asset_id: str, copy_asset_id: str, now: int
) -> int:
    """Conceal the copy from anybody the original was concealed from and the copy is not. No stamp:
    no browser has seen it."""
    concealed_for = 0
    for row in await database.fetch_all(_USERS):
        user_id = str(row["id"])
        if not await access.is_concealed(user_id, source_asset_id):
            continue
        if await access.is_concealed(user_id, copy_asset_id):
            continue
        async with telling(database, Audience.of_user(user_id), About.LIBRARY) as connection:
            await connection.execute(_CONCEAL, (copy_asset_id, user_id, now, now))
        concealed_for += 1
        log.info(
            "lineage.concealed_to_match",
            asset_id=copy_asset_id,
            source_asset_id=source_asset_id,
            user_id=user_id,
        )
    return concealed_for


async def inherit(
    database: Database,
    access: Repository,
    *,
    source_asset_id: str,
    copy_asset_id: str,
    now: int,
) -> Inherited:
    """Copy what is recorded about one asset onto another, in one transaction so tags never arrive
    before the concealment."""
    async with database.write() as connection:
        tag_rows = list(await connection.execute_fetchall(_TAGS_OF, (source_asset_id,)))
        person_rows = list(await connection.execute_fetchall(_PEOPLE_OF, (source_asset_id,)))
        state_rows = list(await connection.execute_fetchall(_CARRIED_STATE, (source_asset_id,)))
        collection_rows = list(
            await connection.execute_fetchall(_COLLECTIONS_OF, (source_asset_id,))
        )

        await connection.execute(_COPY_TAGS, (copy_asset_id, source_asset_id))
        await connection.execute(_COPY_PEOPLE, (copy_asset_id, source_asset_id))
        await connection.execute(_COPY_COLLECTIONS, (copy_asset_id, source_asset_id))

        hidden_for = 0
        for row in state_rows:
            hidden = int(row["hidden"] or 0)
            hidden_for += 1 if hidden else 0
            await connection.execute(
                _WRITE_STATE,
                (
                    copy_asset_id,
                    str(row["user_id"]),
                    row["rating"],
                    hidden,
                    row["hidden_at"] if hidden else None,
                    now,
                ),
            )

        moved = NOBODY
        for row in tag_rows:
            moved |= await bump_stamps_for_object(connection, ObjectType.TAG, str(row["tag_id"]))
        for row in person_rows:
            moved |= await bump_stamps_for_object(
                connection, ObjectType.PERSON, str(row["person_id"])
            )
        for row in collection_rows:
            moved |= await bump_stamps_for_object(
                connection, ObjectType.COLLECTION, str(row["collection_id"])
            )
        announce(moved, About.LIBRARY)

    # After the transaction: the comparison reads the tags and People just written.
    concealed_for = await _match_concealment(
        database,
        access,
        source_asset_id=source_asset_id,
        copy_asset_id=copy_asset_id,
        now=now,
    )

    inherited = Inherited(
        tags=len(tag_rows),
        people=len(person_rows),
        hidden_for=hidden_for,
        ratings=sum(1 for row in state_rows if row["rating"] is not None),
        concealed_for=concealed_for,
        collections=len(collection_rows),
    )
    log.info(
        "lineage.inherited",
        asset_id=copy_asset_id,
        source_asset_id=source_asset_id,
        tags=inherited.tags,
        people=inherited.people,
        hidden_for=inherited.hidden_for,
        concealed_for=inherited.concealed_for,
        collections=inherited.collections,
    )
    return inherited
