# SPDX-License-Identifier: AGPL-3.0-or-later
"""Raising the picture-address counter for everyone one logical object decides for; here because it
reads `acl_grants`."""

from __future__ import annotations

from sift.kernel.audience import Audience
from sift.kernel.db import Connection

#: Every user whose view depends on one logical object: those a grant names and those who hid it.
#: One literal statement per kind, since a table name cannot be bound.
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

#: Every logical object kind. Must match `ObjectType.LOGICAL_TYPES`, held by `test_cache_stamp`
#: since importing it here would be a cycle.
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
    """Make the addresses unreachable for everyone one logical object decides for, and return
    who."""
    # A plain string: the kind arrives as the access layer's enumeration.
    kind = str(object_type)
    statement = _BUMP_FOR_OBJECT.get(kind)
    if statement is None:
        raise ValueError(f"not a logical object: {kind}")
    rows = await connection.execute_fetchall(statement, {"kind": kind, "object": object_id})
    return Audience.of(rows)


#: Everyone a file arriving could become visible to: every user with any grant, since nothing is
#: visible to a guest without one.
_USERS_WITH_A_SHARE = """
SELECT DISTINCT subject_user_id FROM acl_grants WHERE effect = 'share'
"""


async def users_that_may_gain(connection: Connection) -> Audience:
    """Everyone a newly arrived file could become visible to, admins aside."""
    rows = await connection.execute_fetchall(_USERS_WITH_A_SHARE)
    return Audience.of(rows)
