# SPDX-License-Identifier: AGPL-3.0-or-later
"""Giving a file Sift produced what was recorded about the file it came from.

An asset is its bytes, so a compressed copy of something is a different asset: a true statement
that produces an unpleasant result on its own. Compress forty tagged, rated, half-hidden files and
forty strangers arrive in the library: nothing on them, nobody in them, and any of them that came
out of the vault sitting on the ordinary grid in plain sight.

**The rule is: what conceals or describes follows the copy; what exposes it to somebody else does
not.** Stated the other way round, because that is how it is decided rather than how it is coded:

| carried | why |
|---|---|
| hidden | the copy of a concealed thing is the same thing. Not carrying it is a disclosure, and a silent one: nothing on any screen would say a copy had appeared |
| rating | one person's judgement of the content, and the content is the same |
| tags | what the file is, which the copy also is |
| People | who is in it, and the same people are in the copy |
| collections | these pictures belong together, and the copy is one of them. It joins at the end; a photo set is a folder's own pictures in the folder's order, and a copy lands elsewhere, so a set is not carried |

**Concealment is carried as an OUTCOME, not as a column.** Copying the hidden flag across is most of
it and is not all of it, because a file can be concealed by six different things and only two of
them are attached to the file. It can be hidden outright; it can sit in a hidden folder or under a
hidden root; it can belong to a hidden person, tag, collection or site. A copy lands in the same
folder and inherits the tags, the People and the collections, so five of those follow it by
construction. A site, and a second copy of the original sitting in a folder somebody hid, do
not.

Carrying the four and calling it done is the failure this module exists to prevent, so the last
thing that happens here is not a copy of anything: it is a comparison. Every user the original
was concealed from is asked whether the copy is concealed from them too, and where the answer is no
the copy is concealed for them outright. That question is put to the one query that decides what
anybody may see, rather than to a second opinion written here, which is what stops this from
drifting out of step with the rule it is enforcing.

| not carried | why |
|---|---|
| shares and restricts | a grant is a deliberate decision about a specific file. Extending one to a file that did not exist when it was made is deciding on somebody's behalf |
| favorite | a shortlist of things to come back to, and a duplicate of an entry is not a second thing worth coming back to |
| view history, resume point | records of watching *that* file. They are not true of the copy |

It lives in the kernel rather than in the feature that produces copies for the ordinary reason:
every table involved is a kernel table, and two features will want this (one that compresses, one
that edits) before anything else does.
"""

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

# The source is copied with the tag rather than left blank, and that is not a detail. A blank means
# "a person put this here", so dropping it would turn every inherited tag into one somebody is
# recorded as having chosen, and a later sweep for what a stash-box added would miss the copies.
#
# `decided_at` is copied with it for the same reason and one more: the copy carries the moment the
# ORIGINAL was tagged, not the moment the copy was made. What the row records is when somebody
# decided this file is of that thing, and the copy is of the same thing: restamping it with today
# would say a decision was taken that nobody took. A row written before there was such a column
# carries NULL, and NULL comes across as NULL.
_COPY_TAGS = """
INSERT INTO asset_tags (asset_id, tag_id, source, decided_at)
SELECT ?, tag_id, source, decided_at FROM asset_tags WHERE asset_id = ?
ON CONFLICT(asset_id, tag_id) DO NOTHING
"""

_TAGS_OF = "SELECT tag_id FROM asset_tags WHERE asset_id = ?"

# The source and the moment come across here too, as for the tag above: an attribution copied
# without its source would read as a decision somebody made.
_COPY_PEOPLE = """
INSERT INTO asset_people (asset_id, person_id, source, decided_at)
SELECT ?, person_id, source, decided_at FROM asset_people WHERE asset_id = ?
ON CONFLICT(asset_id, person_id) DO NOTHING
"""

_PEOPLE_OF = "SELECT person_id FROM asset_people WHERE asset_id = ?"

# A collection is a claim about the content (these pictures belong together), and a copy of a
# picture is the same picture. It joins each collection the source is in, at the end, as its
# tags, People, rating and hidden come across, or the copy would sit outside every collection
# while looking like it belonged. A photo set
# is deliberately NOT carried: a set is a folder's own pictures in the folder's order, and a copy
# lands elsewhere.
#
# `added_at` comes ACROSS rather than being stamped now, which is the same choice `_COPY_TAGS` and
# `_COPY_PEOPLE` above it make with `decided_at` and it is made for the same reason: the decision
# being copied is the one somebody made about the source, and re-dating it to the moment of the copy
# would say that this membership was decided today. A copy carries the record it inherits.
_COPY_COLLECTIONS = """
INSERT INTO collection_items (collection_id, asset_id, position, added_at)
SELECT held.collection_id, ?,
       (SELECT COALESCE(MAX(after.position) + 1, 0)
          FROM collection_items after
         WHERE after.collection_id = held.collection_id),
       held.added_at
  FROM collection_items held
 WHERE held.asset_id = ?
ON CONFLICT(collection_id, asset_id) DO NOTHING
"""

_COLLECTIONS_OF = "SELECT collection_id FROM collection_items WHERE asset_id = ?"

# Only the two columns that describe the content or conceal it. Everything else on the row
# (favorite, view count, watched time, resume point) is about somebody's history with THAT file
# and is not true of a copy that has never been opened.
#
# `hidden_at` comes across with `hidden`, because a Hidden screen orders by it and a copy that
# arrived concealed did so at the moment it was made, not at the epoch.
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

#: 'sift' in the statement: this is the tag a COPY wears to say Sift made it, so nobody typed it in.
#: And `produced` beside it, which is the pass: the same word `_ATTACH_TAG` writes into the
#: filing a line below, so the tag's own page and the mark on every file wearing it agree about
#: where it came from. Written out rather than bound because this file has exactly one caller and
#: exactly one answer; every path with a choice takes a `Made` instead (see `catalog.py`).
#:
#: The act IS bound (`vocabulary.MADE_ACTS`): which verb made the copy, Compress or the editor,
#: so the tag's Created by line wears that verb's glyph. A tag that is already there keeps what it
#: says, act included: the insert does nothing to it.
_INSERT_TAG = """
INSERT INTO tags (id, name, name_sort, created_at, created_by_kind, created_by_via, created_by_act)
VALUES (?, ?, ?, ?, 'sift', 'produced', ?)
ON CONFLICT(name) DO NOTHING
"""

#: Sift put this one on, so it says so. It is still an ordinary tag in every other respect (it
#: sorts, filters and can be taken off), and the word is only how it got there.
_ATTACH_TAG = """
INSERT INTO asset_tags (asset_id, tag_id, source, decided_at) VALUES (?, ?, 'produced', ?)
ON CONFLICT(asset_id, tag_id) DO NOTHING
"""


async def tag_produced(
    database: Database, *, asset_id: str, tag_name: str, act: str, now: int, new_id: str
) -> str:
    """Put an ordinary tag on a file Sift produced, making the tag if it is not there yet.

    An ordinary tag on purpose. It sorts, filters, searches and bulk-acts exactly like every other
    one, appears on the tag wall beside them, and can be taken off a file or deleted outright by
    somebody who stops wanting it, none of which a special-purpose marker would do without a
    screen of its own being built for it.

    Found by name, and the consequence is worth stating plainly: rename it and the next file Sift
    produces makes a new one under the old name. That is the behaviour somebody expects from a tag
    that applies itself, and the alternative (remembering an id somewhere invisible) makes a
    renamed tag keep filling up with things whose tag no longer says what they are.

    The name collation is case-insensitive, so this cannot end up with two of them.

    `act` is which verb made the file (`vocabulary.MADE_ACTS`), written on a tag this call makes.
    """
    if act not in MADE_ACTS:
        raise ValueError(f"no act is called {act!r}")
    async with database.write() as connection:
        await connection.execute(_INSERT_TAG, (new_id, tag_name, sort_key(tag_name), now, act))
        rows = list(await connection.execute_fetchall(_TAG_BY_NAME, (tag_name,)))
        tag_id = str(rows[0]["id"])
        await connection.execute(_ATTACH_TAG, (asset_id, tag_id, now))
        # Same reason as every other tag change: a tag is what a share or a hide hangs off, so a
        # file joining one changes what some users may see.
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
    """What was carried across, so a caller can log it and a test can assert on it."""

    tags: int
    people: int
    #: How many users the copy arrived concealed from because the ORIGINAL'S OWN ROW said so.
    #: Zero is the ordinary case and non-zero is the one worth being able to see in a log, because
    #: it is the half that cannot be noticed by looking at a screen: something not appearing looks
    #: exactly like it working.
    hidden_for: int
    ratings: int
    #: How many collections the copy joined because the original was in them.
    collections: int = 0
    #: How many users the copy had to be concealed from on top of that, because the original was
    #: concealed from them by something a copy does not inherit: a site, or a second copy of
    #: the original in a folder they hid. Disjoint from `hidden_for` by construction: a
    #: user who got the flag copied across is no longer a user the copy is visible to.
    #:
    #: **Non-zero here means the straightforward half was not enough**, which is the number worth
    #: watching. If it is ever zero across a library that uses collections and hidden sites, this
    #: comparison has stopped asking the question rather than started passing it.
    concealed_for: int = 0


async def _match_concealment(
    database: Database, access: Repository, *, source_asset_id: str, copy_asset_id: str, now: int
) -> int:
    """Conceal the copy from anybody the original was concealed from and the copy is not.

    The check is per user and there are a handful of users on a self-hosted install, so this
    is a couple of cheap reads each and only for users the original is actually hidden from.
    The ordinary case costs one query per user and writes nothing.

    No cache stamp is bumped, and that is deliberate rather than an omission. A stamp bump exists to
    make pictures already sitting in somebody's browser unreachable; this asset was created seconds
    ago and no browser has ever seen a picture of it. Bumping every user's stamp on every
    produced file would throw away every cached thumbnail in the library to protect nothing.
    """
    concealed_for = 0
    for row in await database.fetch_all(_USERS):
        user_id = str(row["id"])
        if not await access.is_concealed(user_id, source_asset_id):
            continue
        if await access.is_concealed(user_id, copy_asset_id):
            continue
        # Told to that user alone: the copy's arrival reached their walls a moment ago.
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
    """Copy what is recorded about one asset onto another. Nothing is moved and nothing is removed.

    One transaction, because the concealment and the tags have to arrive together: a copy that is
    tagged but not yet hidden is, for however long the gap lasts, a concealed thing sitting on the
    grid under a tag that leads straight to it.

    The membership stamps are bumped inside it for the same reason they are on any tag or person
    change (a tag is what a share or a hide is attached to, so a file joining one changes what
    some users may see), and only for the ones that actually moved.
    """
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

    # After the transaction, not inside it. The comparison asks what each user can see, and what
    # they can see depends on the tags and the People that were just written: a copy checked
    # before they landed looks visible to everybody and would be concealed from everybody.
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
