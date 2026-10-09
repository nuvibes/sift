# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a tag takes in: itself and every tag filed under it, walked down from the named tags. UNION,
so a loop ends."""

from __future__ import annotations

import json

from sift.kernel.db import Connection
from sift.kernel.ledger import Actor, record_event
from sift.kernel.vocabulary import Subject

#: Every tag at or under the tags a JSON array names; `{}` is the parameter `constraints.PREDICATES`
#: binds.
TAGS_UNDER = (
    "WITH RECURSIVE tag_down(id) AS ("
    "SELECT value FROM json_each({})"
    " UNION SELECT child.id FROM tags child JOIN tag_down d ON child.parent_id = d.id)"
    " SELECT id FROM tag_down"
)

#: The tags above one tag, nearest first, for refusing a parent that would make a loop.
TAGS_ABOVE = (
    "WITH RECURSIVE tag_up(id, depth) AS ("
    "SELECT parent_id, 1 FROM tags WHERE id = ? AND parent_id IS NOT NULL"
    " UNION SELECT t.parent_id, u.depth + 1 FROM tags t JOIN tag_up u ON t.id = u.id"
    " WHERE t.parent_id IS NOT NULL)"
    " SELECT id, depth FROM tag_up ORDER BY depth"
)


_FILE_UNDER_IF_UNFILED = (
    "UPDATE tags SET parent_id = ? WHERE id = ? AND parent_id IS NULL RETURNING name"
)
_NAME_OF = "SELECT name FROM tags WHERE id = ?"


async def file_under_if_unfiled(
    connection: Connection, tag_id: str, parent_id: str, *, actor: Actor
) -> bool:
    """File a tag under another where it has no parent yet, refusing a loop. False when it was
    not."""
    if tag_id == parent_id:
        return False
    above = await connection.execute_fetchall(TAGS_ABOVE, (parent_id,))
    if any(str(row[0]) == tag_id for row in above):
        return False
    rows = list(await connection.execute_fetchall(_FILE_UNDER_IF_UNFILED, (parent_id, tag_id)))
    if not rows:
        return False
    parent = list(await connection.execute_fetchall(_NAME_OF, (parent_id,)))
    await record_event(
        connection,
        actor=actor,
        verb="edited",
        subject=Subject(kind="tag", id=tag_id, name=str(rows[0][0])),
        payload=json.dumps({"parent": str(parent[0][0]) if parent else None}),
    )
    return True
