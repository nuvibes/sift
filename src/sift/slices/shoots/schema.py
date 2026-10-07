# SPDX-License-Identifier: AGPL-3.0-or-later
"""What this feature records: the shoots it proposed, the ones refused, and the sets it made.

Four tables, and the shape of them follows from one fact: **a proposal is expensive to work out and
cheap to read.** Finding a shoot means asking the meaning index for the neighbours of every
unfiled picture one creator has, which is one lookup per picture. A card on the board cannot pay
that (the board surveys a dozen queues together), so the pass writes what it found and the card
reads rows.

**A proposal is a guess with a shelf life, so it is replaced rather than accumulated.** The pass
clears the proposals it made last time before writing this time's, because a picture that has since
been filed, deleted or attributed to somebody else is not part of the shoot any more and a stale
row would be a card asking about something that is no longer true. Nothing is lost by that: a
refusal, and a Photo Set that was actually made, are recorded in their own tables and survive every
pass.

**A refusal is a standing no, and it is per PICTURE rather than per proposal.** The pass is greedy
and its grouping moves: the same pictures refused as one shoot would come back the next evening as
two, seeded from a different file, with nothing on any screen saying they had already been turned
down. Work that comes back after being dismissed is worse than work that was never offered, so
what is remembered is the pictures (these are not a shoot, do not offer them again) and the
manual Photo Set verb is still there for anybody who changes their mind.

**And the fourth table is the link between a proposal and what it became.** It names the Photo Set
that was made and the receipt that made it, which is what lets the decision be taken back: undo
reads it, deletes the Photo Set it names and nothing else, and leaves every picture where it was.
It also makes a second press harmless, because a proposal that already has a row here has already
been turned into something.
"""

from __future__ import annotations

from sift.kernel.db import Connection, register_schema_initializer

COMPONENT = "shoots"
VERSION = 1

_CREATE_PROPOSALS = """
CREATE TABLE IF NOT EXISTS shoot_proposals (
  id        TEXT PRIMARY KEY,
  person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  name      TEXT NOT NULL,
  found_at  INTEGER NOT NULL
)
"""

# `position` because a shoot is a sequence, exactly as a photo set is: the pictures go in in this
# order, and a proposal that came back shuffled would make a grouping nobody arranged.
#
# `named` is whether this picture already carries the creator the shoot is proposed under. A shoot
# is grown from pictures that do, and the index then finds pictures of the same sitting that carry
# nobody at all: the same room, the same light, and no name on them. Those belong in the grouping
# and they are ALSO the evidence for a second, separate offer: name the rest. Kept as a column
# rather than a second table because it is one fact about one membership row, and asked for by both
# the proposal that makes a set and the proposal that names.
_CREATE_PROPOSAL_ITEMS = """
CREATE TABLE IF NOT EXISTS shoot_proposal_items (
  proposal_id TEXT NOT NULL REFERENCES shoot_proposals(id) ON DELETE CASCADE,
  asset_id    TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  position    INTEGER NOT NULL,
  named       INTEGER NOT NULL,
  PRIMARY KEY (proposal_id, asset_id)
)
"""

_CREATE_REFUSALS = """
CREATE TABLE IF NOT EXISTS shoot_refusals (
  asset_id   TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL
)
"""

# The link between a proposal and the Photo Set it became. `decision_id` is deliberately NOT a
# foreign key: this row has to outlive a build that has forgotten how to read a receipt, and a
# reference to a table another component creates would make the order the two are built in matter
# for no gain at all.
_CREATE_SETS = """
CREATE TABLE IF NOT EXISTS shoot_sets (
  proposal_id  TEXT PRIMARY KEY,
  photo_set_id TEXT NOT NULL,
  decision_id  TEXT,
  made_at      INTEGER NOT NULL
)
"""

_INDEXES = (
    # The card and the panel both read proposals newest first.
    "CREATE INDEX IF NOT EXISTS ix_shoot_proposals_found"
    " ON shoot_proposals(found_at DESC, id DESC)",
    # And the pictures of one proposal, in the order the shoot runs.
    "CREATE INDEX IF NOT EXISTS ix_shoot_items_order"
    " ON shoot_proposal_items(proposal_id, position)",
    # Undo arrives holding the id of what was made, not of the proposal it came from.
    "CREATE INDEX IF NOT EXISTS ix_shoot_sets_made ON shoot_sets(photo_set_id)",
)


async def initialize(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        await connection.execute(_CREATE_PROPOSALS)
        await connection.execute(_CREATE_PROPOSAL_ITEMS)
        await connection.execute(_CREATE_REFUSALS)
        await connection.execute(_CREATE_SETS)
        for statement in _INDEXES:
            await connection.execute(statement)


# It names `assets` and `people` in foreign keys without declaring a dependency on the component
# that creates them: SQLite resolves a foreign key's parent by name when a row is written rather
# than when the table is made, so the reference holds whichever order the two were created in.
register_schema_initializer(COMPONENT, VERSION, initialize, baseline=1)
