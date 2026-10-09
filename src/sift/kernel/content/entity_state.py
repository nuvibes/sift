# SPDX-License-Identifier: AGPL-3.0-or-later
"""What one user has kept at the top of a wall: the pin on every kind of named thing.

Here so the slices share one set of statements; picked as literals, never spliced from names."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from sift.kernel.audience import Audience
from sift.kernel.changes import About, telling
from sift.kernel.content.user_state import OpinionKind, record_opinion
from sift.kernel.db import Connection, Database
from sift.kernel.wire import Wire

__all__ = [
    "EntityStateStore",
    "OpinionSubject",
    "OpinionWrite",
    "PinView",
    "PinWrite",
    "PinnableKind",
    "opinion_before",
]


class PinWrite(Wire):
    """Pin this, or take the pin off; one shape for every kind, as slices cannot share one."""

    pinned: bool


class PinView(Wire):
    """What the server ended up holding, so an optimistic control can settle onto the truth."""

    pinned: bool = False


class PinnableKind(StrEnum):
    """The kinds of named thing that can be pinned; closed, so no caller can name a table."""

    PERSON = "person"
    SITE = "site"
    COLLECTION = "collection"
    TAG = "tag"
    PHOTO_SET = "photo_set"
    SONG = "song"


#: The upsert per kind, written out; `RETURNING` so a matched UPDATE still answers.
_SET_PINNED: dict[PinnableKind, str] = {
    PinnableKind.PERSON: """
INSERT INTO person_user_state (person_id, user_id, pinned, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(person_id, user_id) DO UPDATE SET
    pinned     = excluded.pinned,
    updated_at = excluded.updated_at
RETURNING pinned
""",
    PinnableKind.SITE: """
INSERT INTO site_user_state (site_id, user_id, pinned, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(site_id, user_id) DO UPDATE SET
    pinned     = excluded.pinned,
    updated_at = excluded.updated_at
RETURNING pinned
""",
    PinnableKind.COLLECTION: """
INSERT INTO collection_user_state (collection_id, user_id, pinned, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(collection_id, user_id) DO UPDATE SET
    pinned     = excluded.pinned,
    updated_at = excluded.updated_at
RETURNING pinned
""",
    PinnableKind.TAG: """
INSERT INTO tag_user_state (tag_id, user_id, pinned, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(tag_id, user_id) DO UPDATE SET
    pinned     = excluded.pinned,
    updated_at = excluded.updated_at
RETURNING pinned
""",
    PinnableKind.PHOTO_SET: """
INSERT INTO photo_set_user_state (photo_set_id, user_id, pinned, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(photo_set_id, user_id) DO UPDATE SET
    pinned     = excluded.pinned,
    updated_at = excluded.updated_at
RETURNING pinned
""",
    PinnableKind.SONG: """
INSERT INTO song_user_state (song_id, user_id, pinned, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(song_id, user_id) DO UPDATE SET
    pinned     = excluded.pinned,
    updated_at = excluded.updated_at
RETURNING pinned
""",
}


# The value a write is about to replace, read in its transaction: RETURNING gives only the new
# row, so it is read here once for every writer. No row is NULL, not zero.

#: What an entity opinion can be about; closed, so no caller names a table by accident.
OpinionSubject = Literal["person", "site", "folder", "collection", "tag", "photo_set", "song"]

#: The point read per kind; a folder carries only `hidden`, so asking it for more fails loudly.
_OPINION_BEFORE: dict[str, str] = {
    "person": (
        "SELECT favorite, rating, pinned, hidden FROM person_user_state"
        " WHERE person_id = ? AND user_id = ?"
    ),
    "site": (
        "SELECT favorite, rating, pinned, hidden FROM site_user_state"
        " WHERE site_id = ? AND user_id = ?"
    ),
    "collection": (
        "SELECT favorite, rating, pinned, hidden FROM collection_user_state"
        " WHERE collection_id = ? AND user_id = ?"
    ),
    "tag": (
        "SELECT favorite, rating, pinned, hidden FROM tag_user_state"
        " WHERE tag_id = ? AND user_id = ?"
    ),
    "photo_set": (
        "SELECT favorite, rating, pinned, hidden FROM photo_set_user_state"
        " WHERE photo_set_id = ? AND user_id = ?"
    ),
    "song": (
        "SELECT favorite, rating, pinned, hidden FROM song_user_state"
        " WHERE song_id = ? AND user_id = ?"
    ),
    "folder": "SELECT hidden FROM folder_user_state WHERE folder_id = ? AND user_id = ?",
}

#: Which column each kind is on these tables, which differ from an asset's.
_OPINION_COLUMN: dict[OpinionKind, str] = {
    OpinionKind.FAVORITE: "favorite",
    OpinionKind.RATING: "rating",
    OpinionKind.PIN: "pinned",
    OpinionKind.HIDE: "hidden",
}


async def opinion_before(
    connection: Connection,
    *,
    subject_kind: OpinionSubject,
    subject_id: str,
    user_id: str,
    kind: OpinionKind,
) -> int | None:
    """What this user thought of this thing, read on the caller's own connection."""
    rows = list(
        await connection.execute_fetchall(_OPINION_BEFORE[subject_kind], (subject_id, user_id))
    )
    if not rows:
        return None
    value = rows[0][_OPINION_COLUMN[kind]]
    return None if value is None else int(value)


@dataclass(frozen=True, slots=True)
class OpinionWrite:
    """An opinion a helper will read, write and record in one transaction for its caller."""

    user_id: str
    subject_kind: OpinionSubject
    subject_id: str
    kind: OpinionKind
    after: int | None


class EntityStateStore:
    """Reads and writes the pin on every kind of named thing."""

    def __init__(self, database: Database) -> None:
        self._db = database

    async def set_pinned(
        self, kind: PinnableKind, entity_id: str, user_id: str, *, pinned: bool
    ) -> bool:
        """Pin one thing or unpin it, recording the opinion in one write; returns what it is."""
        now = int(time.time())
        async with telling(self._db, Audience.of_user(user_id), About.MINE) as connection:
            # The pinnable kinds are spelled as opinion subjects, so the value serves.
            about: OpinionSubject = kind.value
            before = await opinion_before(
                connection,
                subject_kind=about,
                subject_id=entity_id,
                user_id=user_id,
                kind=OpinionKind.PIN,
            )
            rows = list(
                await connection.execute_fetchall(
                    _SET_PINNED[kind],
                    (entity_id, user_id, int(pinned), now),
                )
            )
            await record_opinion(
                connection,
                user_id=user_id,
                subject_kind=about,
                subject_id=entity_id,
                kind=OpinionKind.PIN,
                before=before,
                after=int(pinned),
                at=now,
            )
        # An empty answer cannot happen; this reports what was asked rather than a false False.
        return bool(rows[0]["pinned"]) if rows else pinned
