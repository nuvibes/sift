# SPDX-License-Identifier: AGPL-3.0-or-later
"""Raising the picture-address counter for everyone one logical object decides things for.

The counter itself, and the two blunter ways of raising it (one user's, and everybody's),
live in `sift.kernel.cache_stamp`, at the top of the kernel where the content layer can reach them.
This third way cannot live beside them, and the reason is the whole of why this module exists: it
has to read `acl_grants`, and that table is this layer's. Nothing outside the access layer may name
it, in code or in a comment, because a second place that reads the grants is a second place that
can get a permission question wrong.

Nothing here answers a permission question. It asks narrower ones (who does this object decide
things for, and who has been given anything at all), and every caller is above this layer, so
putting it here costs nobody a cycle.
"""

from __future__ import annotations

from sift.kernel.audience import Audience
from sift.kernel.db import Connection

#: Every user whose view of the library depends on one logical object: those a grant on it names,
#: and those who have hidden it. Both halves matter and they overlap in neither direction: a share
#: is somebody else's permission to see the object's files, and hiding is this user's decision to
#: stop seeing them.
#:
#: One statement per kind written out, differing only in the table they read. A tag, a person, a
#: collection, a site, a photo set and a song each keep their own table of per-user state, and a
#: table name cannot be a bound parameter, so the alternative is assembling SQL from a string, in
#: the one part of the application where that is never done. With no ORM here, parameterization
#: is the whole of the injection defence, and it only holds while every statement is a literal.
_BUMP_FOR_TAG = """
UPDATE users SET cache_stamp = cache_stamp + 1
 WHERE id IN (SELECT subject_user_id FROM acl_grants
               WHERE object_type = :kind AND object_id = :object)
    OR id IN (SELECT user_id FROM tag_user_state WHERE tag_id = :object AND hidden = 1)
RETURNING id
"""

_BUMP_FOR_PERSON = """
UPDATE users SET cache_stamp = cache_stamp + 1
 WHERE id IN (SELECT subject_user_id FROM acl_grants
               WHERE object_type = :kind AND object_id = :object)
    OR id IN (SELECT user_id FROM person_user_state WHERE person_id = :object AND hidden = 1)
RETURNING id
"""

_BUMP_FOR_COLLECTION = """
UPDATE users SET cache_stamp = cache_stamp + 1
 WHERE id IN (SELECT subject_user_id FROM acl_grants
               WHERE object_type = :kind AND object_id = :object)
    OR id IN (SELECT user_id FROM collection_user_state
               WHERE collection_id = :object AND hidden = 1)
RETURNING id
"""

_BUMP_FOR_SITE = """
UPDATE users SET cache_stamp = cache_stamp + 1
 WHERE id IN (SELECT subject_user_id FROM acl_grants
               WHERE object_type = :kind AND object_id = :object)
    OR id IN (SELECT user_id FROM site_user_state
               WHERE site_id = :object AND hidden = 1)
RETURNING id
"""

_BUMP_FOR_PHOTO_SET = """
UPDATE users SET cache_stamp = cache_stamp + 1
 WHERE id IN (SELECT subject_user_id FROM acl_grants
               WHERE object_type = :kind AND object_id = :object)
    OR id IN (SELECT user_id FROM photo_set_user_state
               WHERE photo_set_id = :object AND hidden = 1)
RETURNING id
"""

_BUMP_FOR_SONG = """
UPDATE users SET cache_stamp = cache_stamp + 1
 WHERE id IN (SELECT subject_user_id FROM acl_grants
               WHERE object_type = :kind AND object_id = :object)
    OR id IN (SELECT user_id FROM song_user_state
               WHERE song_id = :object AND hidden = 1)
RETURNING id
"""

#: Every logical object, by the name the access layer calls each kind. Anything not in here has no
#: membership to change (see `bump_stamps_for_object`).
#:
#: It has to hold exactly what `ObjectType.LOGICAL_TYPES` holds, and it cannot say so here: the
#: access layer cannot be imported from this module without a cycle. That agreement is therefore a
#: GATE rather than a line of code (`test_cache_stamp` puts the two side by side), and it exists
#: because a logical object added there and forgotten here is not a missing bump. It is a
#: `ValueError` out of the middle of a write, as a 500, on whichever screen edits that kind of
#: membership.
_BUMP_FOR_OBJECT = {
    "tag": _BUMP_FOR_TAG,
    "person": _BUMP_FOR_PERSON,
    "collection": _BUMP_FOR_COLLECTION,
    "site": _BUMP_FOR_SITE,
    "photo_set": _BUMP_FOR_PHOTO_SET,
    "song": _BUMP_FOR_SONG,
}


async def bump_stamps_for_object(
    connection: Connection, object_type: str, object_id: str
) -> Audience:
    """Make the addresses unreachable for everyone one logical object decides for, and say who.

    For the third kind of change, which is neither one user's nor everybody's: a file joining or
    leaving a tag, a person, a collection, a site, a photo set or a song. Membership is not a
    permission, but permissions are attached to it: taking a file out of a shared collection ends
    a user's access to it as surely as revoking the share does, and putting one into a collection
    somebody has hidden conceals it from them without anything of theirs being touched.

    Neither of the two above fits. The user whose view changed is not the one making the change,
    so there is no id to pass; and everybody is far too many, because tagging a file would empty the
    grid of users the tag means nothing to.

    So it asks the object instead: who has a grant on this, and who has hidden it. On an install
    with one user and nothing shared that is nobody, and this costs one indexed statement that
    updates no rows, which is what makes it affordable on a path a scan takes per file.

    It hands that set back rather than throwing it away, because the change bus needs exactly it:
    the users to tell that what they may see has moved. Still one statement (SQLite reports the
    rows a write touched when asked), so the usual answer here is still an empty set and no work.

    Refuses anything that is not a logical object. The physical objects (a root, a folder, a file)
    have no membership to change; a file moves between folders by being re-imported, and the
    folder's own vault flag is one user's business and takes `bump_cache_stamp`.
    """
    # Through `str` so that what reaches the database is a plain one. Callers name the kind with
    # the access layer's enumeration, which cannot be imported here without a cycle and must not be
    # bound as itself.
    kind = str(object_type)
    statement = _BUMP_FOR_OBJECT.get(kind)
    if statement is None:
        raise ValueError(f"not a logical object: {kind}")
    rows = await connection.execute_fetchall(statement, {"kind": kind, "object": object_id})
    return Audience.of(rows)


#: Everyone a file arriving in the library could possibly become visible to.
#:
#: Not the same question as "who can see this file", and deliberately a wider one. That question is
#: the resolver in the repository (the recursive walk down roots and folders where the nearest
#: decision wins), and it is bound to one viewer at a time. Asking it per user per imported file
#: would put the permission resolver on the slowest path in the application, inside the write, and a
#: second, looser copy of it here would be far worse: two statements that decide who sees what drift,
#: and the drift is silent until the day one of them says yes.
#:
#: So this asks the one thing that IS cheap and is enough. Nothing is visible to a non-admin without
#: an explicit share (the resolver's last word on a user with no grant reaching a file is no),
#: so a user who has never been given anything cannot have gained a view of a file that has just
#: appeared, and is not told. Everyone else is told that something arrived, and re-reads their own
#: page through the ordinary door, which answers exactly and answers for itself.
#:
#: The message carries no id and no name, so what a share-holder learns from one that turns out not
#: to concern them is that the library gained a file. What it buys is that files landing in a folder
#: somebody already has follow the share they already have, without anybody refreshing anything.
_USERS_WITH_A_SHARE = """
SELECT DISTINCT subject_user_id FROM acl_grants WHERE effect = 'share'
"""


async def users_that_may_gain(connection: Connection) -> Audience:
    """Everyone a newly arrived file could become visible to, admins aside.

    Admins see the whole library and are reached by the audience this is combined with, which names
    them as a reach rather than as a set: who is an admin is a question with an answer that moves,
    and it is settled at the moment of sending rather than here.

    One indexed statement that updates nothing, on an install with nothing shared returning no rows
    at all, which is what makes it affordable on the path a scan takes per file, where it sits
    beside hashing every byte of the file it is about.
    """
    rows = await connection.execute_fetchall(_USERS_WITH_A_SHARE)
    return Audience.of(rows)
