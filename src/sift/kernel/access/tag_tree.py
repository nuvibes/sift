# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a tag takes in: itself and every tag filed under it, written once.

A tag may have one parent (`tags.parent_id`), so the tags form a tree: `Beach` filed under
`Outdoors`. Asking for the files under `Outdoors` means the files carrying `Outdoors` or anything
whose parent chain arrives at it, so a filter on a parent takes in its whole branch.

**DOWN from the named tags, not up from every tag.** The walk starts from the ids a filter names,
so it costs one step per level under them rather than a pass over the whole table.

**UNION and not UNION ALL**, so a parent loop in a hand-edited or restored database ends instead of
recursing for ever: a tag already in the set is not added twice. The writers refuse a loop
(the record form's save and `file_under_if_unfiled`), and this keeps a reader safe from one that
arrived another way.

It is a whole SELECT rather than the body of a named CTE, so it drops into an IN without the
statement around it having to own a `WITH`.
"""

from __future__ import annotations

import json

from sift.kernel.db import Connection
from sift.kernel.ledger import Actor, record_event
from sift.kernel.vocabulary import Subject

#: Every tag at or under the tags a JSON array names. `{}` is the parameter holding that array,
#: filled by the one caller that counts and binds it (`constraints.PREDICATES`); nothing that
#: arrives at run time is ever put through the text.
#:
#: `tag_down` is named so it cannot collide with a CTE in the statement this is spliced into.
TAGS_UNDER = (
    "WITH RECURSIVE tag_down(id) AS ("
    "SELECT value FROM json_each({})"
    " UNION SELECT child.id FROM tags child JOIN tag_down d ON child.parent_id = d.id)"
    " SELECT id FROM tag_down"
)

#: The tags above one tag, nearest first, with how far up each is. `?` is the tag. Used by the
#: writers to refuse a parent that is the tag itself or already somewhere under it.
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
    """File a tag under another where it has no parent yet, and record it. False when it was not.

    For an import, which fills what is empty and never moves a tag somebody has already filed. A
    parent that is the tag itself or already somewhere under it is refused, as the record form
    refuses one, so a loop cannot arrive this way either. Runs in the caller's write.
    """
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
