# SPDX-License-Identifier: AGPL-3.0-or-later
"""Writing down that one file was made from another, and reading it back, in one place for both
halves."""

from __future__ import annotations

import json

from sift.kernel.access import Repository, Viewer
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor, Object, record_event
from sift.kernel.reach import OUT_OF_REACH
from sift.kernel.vocabulary import VIA_PRODUCED, Subject
from sift.slices.media_edit.models import MadeCopy, Produced
from sift.slices.media_edit.refusals import NotFound

_USER_STILL_THERE = "SELECT 1 FROM users WHERE id = ?"

_INSERT_PRODUCED = """
INSERT INTO produced_files
  (id, asset_id, source_asset_id, operation, preset, target_bytes, produced_by, produced_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(asset_id) DO UPDATE SET
  source_asset_id = excluded.source_asset_id,
  operation = excluded.operation,
  preset = excluded.preset,
  target_bytes = excluded.target_bytes,
  produced_by = excluded.produced_by,
  produced_at = excluded.produced_at
"""

_PRODUCED_FOR = """
SELECT asset_id, source_asset_id, operation, preset, target_bytes, produced_at
  FROM produced_files WHERE asset_id = ?
"""

#: Capped: a file trimmed thirty times is a list, not a fact.
_MADE_FROM = """
SELECT asset_id, operation, produced_at
  FROM produced_files WHERE source_asset_id = ?
  ORDER BY produced_at DESC
  LIMIT ?
"""


MOST_COPIES = 12


async def made_from(
    database: Database, access: Repository, asset_id: str, *, viewer: Viewer
) -> list[MadeCopy]:
    """The copies made from this file, for the original's page; ones the user may not see are left
    out."""
    if await access.open_asset(viewer, asset_id) is None:
        raise NotFound(OUT_OF_REACH)

    copies: list[MadeCopy] = []
    for row in await database.fetch_all(_MADE_FROM, (asset_id, MOST_COPIES)):
        made = str(row["asset_id"])
        locations = await access.locations(viewer, made)
        if not locations:
            continue
        copies.append(
            MadeCopy(
                asset_id=made,
                filename=locations[0].filename,
                operation=str(row["operation"]),
                produced_at=int(row["produced_at"]),
            )
        )
    return copies


async def record(
    database: Database,
    *,
    asset_id: str,
    source_asset_id: str,
    operation: str,
    preset: str | None,
    target_bytes: int | None,
    actor_id: str | None,
    now: int,
) -> None:
    """Record that this file was made from that one, with the verb pressed, and the ledger's
    `produced` event
    in the same transaction, which outlives the row's cascades."""
    async with database.write() as connection:
        await connection.execute(
            _INSERT_PRODUCED,
            (new_id(), asset_id, source_asset_id, operation, preset, target_bytes, actor_id, now),
        )
        # Looked up in this transaction: the asker may have been removed during the encode.
        asker = (
            actor_id
            if actor_id and await connection.execute_fetchall(_USER_STILL_THERE, (actor_id,))
            else None
        )
        await record_event(
            connection,
            actor=Actor.user(asker) if asker else Actor.sift(VIA_PRODUCED),
            verb="produced",
            subject=Subject(kind="asset", id=asset_id),
            object=Object(kind="asset", id=source_asset_id),
            payload=json.dumps({"operation": operation}),
        )


async def produced_for(
    database: Database, access: Repository, asset_id: str, *, viewer: Viewer
) -> Produced | None:
    """Where this file came from, for its own page; the original's name only if the user may see it."""
    if await access.open_asset(viewer, asset_id) is None:
        raise NotFound(OUT_OF_REACH)
    row = await database.fetch_one(_PRODUCED_FOR, (asset_id,))
    if row is None:
        return None

    source_id = row["source_asset_id"]
    source_filename: str | None = None
    visible_source: str | None = None
    if source_id is not None:
        locations = await access.locations(viewer, str(source_id))
        if locations:
            visible_source = str(source_id)
            source_filename = locations[0].filename
    return Produced(
        asset_id=str(row["asset_id"]),
        source_asset_id=visible_source,
        source_filename=source_filename,
        operation=str(row["operation"]),
        preset=None if row["preset"] is None else str(row["preset"]),
        target_bytes=row["target_bytes"],
        produced_at=int(row["produced_at"]),
    )
