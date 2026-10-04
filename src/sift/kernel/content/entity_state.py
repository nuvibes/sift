# SPDX-License-Identifier: AGPL-3.0-or-later
"""What one user has KEPT AT THE TOP of a wall: the pin, for all five kinds of named thing.

A pin says "whatever else this list is doing, these few belong first". It is an opinion held per
user, exactly as the heart and the stars beside it are, so two people sharing an install pin
their own walls and neither can see the other's.

## Why this is in the kernel and not in five slices

The heart and the stars are written from four different slices: People (which also owns
Sites), Collections, Photo sets and Tags. Written in each, the same statement would be typed out
four times in four shapes, and "where is an entity opinion written" would take four files.

So the same argument the asset's own store makes applies here word for word: several features touch
one set of tables, and shared data that lives inside one feature makes the others depend on that
feature. Here they all depend on the kernel instead, which is the direction dependencies are
allowed to run, and a sixth opinion added later is one file rather than a hunt.

## Why five statements written out rather than one built from a table name

Because building SQL out of a table's name is how a column name comes to be treated as data, which
is the argument already written beside the heart's own upserts. This picks a LITERAL out of a
mapping; nothing here is ever spliced. Five statements standing side by side is also the form in
which a difference between them is visible, which four files apart it is not.

## What is not here

**Usernames.** A username on a Site carries no heart, no stars and no pin: a Person is the
identity and a Username is one of the names they post under, so an opinion belongs one level up.

**Files.** Not because a wall of media is not a list of names: a pin is about keeping something
WHERE YOU PUT IT, and a wall of media is a list somebody scrolls exactly as a wall of names is. A
file's pin lives on `asset_user_state` beside its heart (see `kernel.content.user_state`),
because a file's opinions are one row and a foreign key cannot be polymorphic; it is not here for
that reason and no other.

**Loops.** Not for a reason, but for want of a table: a loop is a piece of a video and has no
per-user opinion row of its own. Nothing about the design refuses it.

These tables carry no permissions. Whether the viewer may see the thing they are pinning is the
access layer's question, and every route here asks it before calling in.
"""

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
    """Pin this, or take the pin off.

    ONE shape for all five kinds rather than one per slice, which is what the heart next door has.
    A slice may not import another slice, so four copies of `favorite: bool` is what that costs,
    and four copies is how a fifth one ends up spelled differently. The kernel is the one place all
    five may reach, and `kernel.changes` already publishes wire shapes from here.
    """

    pinned: bool


class PinView(Wire):
    """What the server ended up holding, so an optimistic control can settle onto the truth."""

    pinned: bool = False


class PinnableKind(StrEnum):
    """The six kinds of named thing somebody can keep at the top of a wall.

    A closed set rather than a string, so a caller cannot name a table. The values are the words the
    rest of Sift already uses for these (the same spellings the entity tab strip is built from),
    because a second vocabulary for one set of things is how two lists come to disagree.
    """

    PERSON = "person"
    SITE = "site"
    COLLECTION = "collection"
    TAG = "tag"
    PHOTO_SET = "photo_set"
    SONG = "song"


#: The upsert per kind. Written out, one per line of table, for the reason the module docstring
#: gives, and `RETURNING pinned` because a caller reading the answer as "did it land" needs the
#: statement to say so: without it an UPDATE that matched a row answers with no rows, which is
#: indistinguishable from having matched none.
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


# --- what a named thing was worth to somebody BEFORE the write about to happen -------------------
#
# Every write below and in the four slices that own these tables REPLACES what was there: a heart
# comes off, stars move from seven to nine, a hide is undone, and the row afterwards says only what
# is true now. `kernel.content.user_state.record_opinion` appends the row that remembers it, in the
# write's own transaction, and it has to be TOLD the value it replaced, because nothing can read
# that back afterwards. The upsert's own `RETURNING` cannot answer it either: SQLite returns the row
# AFTER the statement, so on an update it hands back exactly what was just written.
#
# So the point read lives here, once, for the same reason the five pins do: the entity opinions are
# written from four slices and the access layer, and a `SELECT` typed out at each of those is five
# chances to read a different column or key it on the wrong pair.
#
# No row at all is NULL rather than zero: "it was off" and "there was nothing here" are different
# answers, and only one of them is true for a first heart.

#: What an entity opinion can be about, in the words `kernel.vocabulary.SubjectKind` already uses.
#: A closed set rather than a string, so a caller cannot name a table by accident.
OpinionSubject = Literal["person", "site", "folder", "collection", "tag", "photo_set", "song"]

#: The point read per kind. Each names the columns its own table actually carries, which is why the
#: folder's is one column: a folder can be hidden and nothing else: there is no heart, no stars
#: and no pin on a folder, so asking one for a rating is a caller's mistake and reads back as a
#: missing column rather than as a quiet NULL.
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

#: Which column each kind of opinion is about on these tables.
#:
#: Its own mapping rather than the one beside `user_state._OPINION_BEFORE`, and the difference is
#: the tables and not a second vocabulary: an asset carries `o_count` and no entity does, and an
#: entity's `hidden` is an opinion row here while a file's hide is a ledger event. Two column sets,
#: so two mappings; the KINDS are the one closed set, imported rather than repeated.
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
    """What this user thought of this thing, read on the caller's own connection.

    The caller's connection and never a fresh one, for the reason `record_opinion` takes one: this
    is the first half of a write that is already open, and a value read outside that transaction
    could be one another write has since replaced.
    """
    rows = list(
        await connection.execute_fetchall(_OPINION_BEFORE[subject_kind], (subject_id, user_id))
    )
    if not rows:
        return None
    value = rows[0][_OPINION_COLUMN[kind]]
    return None if value is None else int(value)


@dataclass(frozen=True, slots=True)
class OpinionWrite:
    """The opinion a write is about to replace, for a writer that does not own its transaction.

    The same shape, and for the same reason, as the `Recording` the access layer hands its own
    write helper: a concealment there is a statement and a dict of named parameters given to one
    helper that opens the transaction, so the writer itself has no connection to append the row on.
    It hands this instead, and the helper reads the value before, writes, and records, all three
    inside the one transaction, which is the only arrangement in which the row cannot be missing
    for a press that happened.

    `after` is the value the statement is about to write, because nothing else in the helper knows
    which of the columns this write is the one about.
    """

    user_id: str
    subject_kind: OpinionSubject
    subject_id: str
    kind: OpinionKind
    after: int | None


class EntityStateStore:
    """Reads and writes the pin on a person, a Site, a collection, a tag or a photo set."""

    def __init__(self, database: Database) -> None:
        self._db = database

    async def set_pinned(
        self, kind: PinnableKind, entity_id: str, user_id: str, *, pinned: bool
    ) -> bool:
        """Pin one thing, or take the pin off. Returns what it is now.

        The audience is exactly one user and it is the one who wrote it, whose other tabs are
        drawing the same wall: the same reasoning the heart follows, and the reason this cannot
        use the every-admin audience beside it: a pin changes nobody's screen but the pinner's.

        The opinion row is appended in the same transaction as the upsert, and the value it
        replaced is read one statement before it: a pin taken off is a pin that was there, and the
        upsert is the thing that destroys the evidence of it.
        """
        now = int(time.time())
        async with telling(self._db, Audience.of_user(user_id), About.MINE) as connection:
            # The five pinnable kinds ARE five of the six an opinion can be about, spelled the
            # same way, which is why this is the enum's own value and not a second mapping.
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
        # An empty answer cannot happen (the statement inserts when there is no row and updates
        # when there is), so this reports what was asked for rather than inventing a False that
        # would say the pin came off. See the RETURNING note above the statements.
        return bool(rows[0]["pinned"]) if rows else pinned
