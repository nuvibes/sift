# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the record does when two things turn out to be one.

A person merge and a Site merge each move the tables that point at their subject by a KEY. Each
has its own literal list, `slices/people/merge.py` and `site_merge.py`, and a schema check holds
each list to every `REFERENCES` in the database. This is the other half, and it is the same for
both: the stores that name a thing by an id with a KIND WORD beside it rather than by a key. A
stash-box run, a kept answer and an undecided one (`subject` + `local_id`), a ledger event's
subjects and its object (`kind` + `subject_id`, `object_kind` + `object_id`), and the history of
what somebody thought (`subject_kind` + `subject_id`).

No key reaches back from any of them, so nothing cascades and nothing notices: left alone, every row
stays under an id nothing answers to: a line like "Linked to FansDB \u2014 by hand" would lose its
run and read as "Linked to FansDB", and the survivor's page would lose every line about the one
going.

## One list, both merges

The statements are written once, here, with the kind as a bound value: literal SQL, nothing
assembled from a name. Two copies of this block, one per merge, would let one merge lack what the
other gains.

## The rule

The ledger names a subject by a SNAPSHOT: the id says where the thing is, the name says what it
was called when the act was taken (`vocabulary.Subject`, `history_events.Thing`). A merge is not a
deletion (the one going turned out to be the survivor), so the ID follows and the NAME stays as
written. A row that never wrote a name is given the name of the one going, never the survivor's:
left NULL it would be read live, and live is the survivor's name, which is the past rewritten.

Where the survivor already holds the row (an event naming both, a kept answer from the same box
for the same field) the survivor's own wins: `UPDATE OR IGNORE`, then the leftover is cleared,
except in the ledger, where nothing is ever removed and the row naming the one going simply
stays, reading as a thing that has gone.

**The merge's own event is never re-pointed.** Its subject is the one going, and that is the whole
of what it says: turned round onto the survivor it would read "Merged into <this page>" on the
survivor's own page. A live merge writes its own event after this runs, so the clause holds the
rule for a merge already on the record.
"""

from __future__ import annotations

from collections.abc import Mapping

from sift.kernel.db import Connection
from sift.kernel.migrations import table_exists

#: Every store that names a person or a Site by an id with a kind word beside it, and what a merge
#: does to it, as (the table, the statement), in the order they run. Each statement is bound
#: `{kind, keeping, losing, name}`; a statement that has no use for one simply does not name it.
#:
#: The table is carried beside its statement so a store that is not in this database (a process
#: that never registered the stash-box component, a catalog step running before it) is skipped
#: rather than failing the whole merge, and so the schema check can read the set off this list.
FOLLOWS: tuple[tuple[str, str], ...] = (
    # WHAT A STASH-BOX WAS ASKED. One row per ask (catalog v51), so nothing collides: all of them
    # move, and "FansDB filled in 10 details" follows the box link that already moved.
    (
        "enrichment_runs",
        "UPDATE enrichment_runs SET local_id = :keeping WHERE subject = :kind AND local_id = :losing",
    ),
    # WHAT WAS SETTLED ABOUT ITS ANSWERS. One row per subject, box and field, and one per subject:
    # the survivor's own wins where both have one.
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
    # WHAT HAPPENED TO IT: every event it was a subject of, name kept, except the merge's own.
    (
        "workbench_decision_subjects",
        "UPDATE OR IGNORE workbench_decision_subjects"
        " SET subject_id = :keeping, name = COALESCE(name, :name)"
        " WHERE kind = :kind AND subject_id = :losing"
        " AND decision_id NOT IN (SELECT id FROM workbench_decisions WHERE verb = 'merged')",
    ),
    # WHAT WAS DONE WITH IT: a file named after them, a file filed under it.
    (
        "workbench_decisions",
        "UPDATE workbench_decisions"
        " SET object_id = :keeping, object_name = COALESCE(object_name, :name)"
        " WHERE object_kind = :kind AND object_id = :losing",
    ),
    # WHAT SOMEBODY THOUGHT OF IT: one row per press, no name, nothing to collide.
    (
        "opinions",
        "UPDATE opinions SET subject_id = :keeping WHERE subject_kind = :kind AND subject_id = :losing",
    ),
    # A FILTER KEPT BY ID: a saved filter's address and a saved Theater wall's cell hold the ids
    # they name inside their text (`slices/search/stored.py`), with no kind word beside them. An id
    # is a whole word no other id or name contains, so it is replaced where it stands: left alone,
    # the filter would read "a Site that no longer exists" and match nothing the survivor holds.
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

#: The stores that ALSO name a thing by an id with a kind word beside it, and why a merge leaves
#: each one alone. Read by the schema check beside `FOLLOWS`: a new store of this shape must be put
#: in one list or the other, so the question "does a merge lose it" is answered on the day it is
#: written rather than on the day somebody counts rows.
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
    """Re-point every kind-word store from the one going to the survivor, inside the caller's
    transaction. `name` is what the one going was called (see the module header); None only where
    nothing recorded it, and then a nameless row stays nameless rather than borrowing a word."""
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
