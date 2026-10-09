# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shoot tables: proposals are replaced each pass; per-picture refusals and made sets persist."""

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

# `position`: a shoot is a sequence. `named`: the picture already carries the creator; the ones
# that do not are the evidence for Name the rest.
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

# `decision_id` is not a foreign key so this row outlives a build that cannot read receipts.
_CREATE_SETS = """
CREATE TABLE IF NOT EXISTS shoot_sets (
  proposal_id  TEXT PRIMARY KEY,
  photo_set_id TEXT NOT NULL,
  decision_id  TEXT,
  made_at      INTEGER NOT NULL
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_shoot_proposals_found"
    " ON shoot_proposals(found_at DESC, id DESC)",
    "CREATE INDEX IF NOT EXISTS ix_shoot_items_order"
    " ON shoot_proposal_items(proposal_id, position)",
    # Undo holds the id of the set made, not of the proposal.
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


# SQLite resolves a foreign key's parent when a row is written, so creation order does not matter.
register_schema_initializer(COMPONENT, VERSION, initialize, baseline=1)
