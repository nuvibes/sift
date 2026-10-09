# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the record does when two things turn out to be one: the stores naming a thing by an id and a
kind word follow it."""

from __future__ import annotations

from collections.abc import Mapping

from sift.kernel.db import Connection
from sift.kernel.migrations import table_exists

#: Every store that names a person or a Site by an id and a kind word, with what a merge does to it,
#: in order. The table rides along so a store this database lacks is skipped. The id follows; a name
#: written stays as written, and the merge's own event is never re-pointed.
FOLLOWS: tuple[tuple[str, str], ...] = (
    # One row per ask, so all of them move.
    (
        "enrichment_runs",
        "UPDATE enrichment_runs SET local_id = :keeping WHERE subject = :kind AND local_id = :losing",
    ),
    # The survivor's own wins where both have one.
    (
        "stash_box_kept",
        "UPDATE OR IGNORE stash_box_kept SET local_id = :keeping"
        " WHERE subject = :kind AND local_id = :losing",
    ),
    ("stash_box_kept", "DELETE FROM stash_box_kept WHERE subject = :kind AND local_id = :losing"),
    (
        "stash_box_undecided",
        "UPDATE OR IGNORE stash_box_undecided SET local_id = :keeping"
        " WHERE subject = :kind AND local_id = :losing",
    ),
    (
        "stash_box_undecided",
        "DELETE FROM stash_box_undecided WHERE subject = :kind AND local_id = :losing",
    ),
    (
        "workbench_decision_subjects",
        "UPDATE OR IGNORE workbench_decision_subjects"
        " SET subject_id = :keeping, name = COALESCE(name, :name)"
        " WHERE kind = :kind AND subject_id = :losing"
        " AND decision_id NOT IN (SELECT id FROM workbench_decisions WHERE verb = 'merged')",
    ),
    (
        "workbench_decisions",
        "UPDATE workbench_decisions"
        " SET object_id = :keeping, object_name = COALESCE(object_name, :name)"
        " WHERE object_kind = :kind AND object_id = :losing",
    ),
    (
        "opinions",
        "UPDATE opinions SET subject_id = :keeping WHERE subject_kind = :kind AND subject_id = :losing",
    ),
    # A saved filter or Theater cell holds ids inside its text (`slices/search/stored.py`), replaced
    # where they stand.
    (
        "saved_searches",
        "UPDATE saved_searches SET query = replace(query, :losing, :keeping)"
        " WHERE instr(query, :losing) > 0",
    ),
    (
        "theater_cells",
        "UPDATE theater_cells SET source = replace(source, :losing, :keeping)"
        " WHERE instr(source, :losing) > 0",
    ),
)

#: The stores that also name a thing by an id and a kind word, and why a merge leaves each alone.
NOT_FOLLOWED: Mapping[str, str] = {
    "visibility_members": (
        "What a file held per counted kind when a write first reached it, by the kind word the"
        " counts use; folded and cleared by the end of that write, so a merge never finds a row"
    ),
    "acl_grants": (
        "a grant is a decision about one specific thing, so both merges forget the grants of the "
        "one going before they start (`Repository.forget_object`): losing access is the safe way "
        "to be wrong"
    ),
    "viewer_entity_counts": (
        "a count derived from the file links, kept by `visibility.py` as the links a merge moves "
        "change hands: a cache of the rows above, not a fact of its own"
    ),
    "viewer_partner_counts": "the same derived counts, per pair of things",
    "visibility_filing": (
        "a share's files still being decided, a page at a time from the facts as they stand; the "
        "grant it names is forgotten with the thing going, so its pages then decide nothing"
    ),
    "workbench_press_objects": (
        "a total of the record per press, kept by the record's own triggers as a merge rewrites "
        "what each act was done with"
    ),
    "workbench_press_subjects": "the same totals per press, of what each act was about",
    "search_events": (
        "a user's own record of what they searched for and opened, pruned at a year: a log of "
        "what was typed then, not a pointer to follow"
    ),
    "search_history": "a user's recent searches: the words they typed, not a pointer to follow",
    "search_opens": (
        "a file opened from a wall a search narrowed: its `subject` is the words typed, not a kind"
        " word, and what it points at is a file, never a person or a Site"
    ),
}


async def follow(
    connection: Connection, *, kind: str, losing: str, keeping: str, name: str | None
) -> None:
    """Re-point every kind-word store from the one going to the survivor, in the caller's
    transaction."""
    bound = {"kind": kind, "losing": losing, "keeping": keeping, "name": name}
    present: dict[str, bool] = {}
    for table, statement in FOLLOWS:
        if table not in present:
            present[table] = await table_exists(connection, table)
        if present[table]:
            await connection.execute(statement, bound)


def tables_that_follow() -> frozenset[str]:
    """Every store `follow` touches, for the check that holds the two lists to the schema."""
    return frozenset(table for table, _ in FOLLOWS)
