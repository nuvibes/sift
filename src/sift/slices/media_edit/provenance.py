# SPDX-License-Identifier: AGPL-3.0-or-later
"""Writing down that one file was made from another, and reading it back.

Plain functions over the database rather than methods on either service, because BOTH halves of
this slice produce files (a smaller copy and an edited one) and they write the same row. Two
services each holding their own copy of the INSERT is how the two answers start to differ, and the
difference would show up as a copy whose page has no link back rather than as anything that fails.

What the row is for is in the schema module. What matters here is that there is one way to write it
and one way to read it.
"""

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

#: The other direction: what was made FROM this file. Newest first, and capped, because a file
#: somebody has trimmed thirty times is a list rather than a fact.
_MADE_FROM = """
SELECT asset_id, operation, produced_at
  FROM produced_files WHERE source_asset_id = ?
  ORDER BY produced_at DESC
  LIMIT ?
"""


#: How many copies a file's own page will name.
MOST_COPIES = 12


async def made_from(
    database: Database, access: Repository, asset_id: str, *, viewer: Viewer
) -> list[MadeCopy]:
    """The copies made from this file, for the original's own page.

    The reverse of `produced_for`, and scoped the same way: each copy is resolved through the
    access layer, so one this user may not see is left out entirely rather than named. Empty is
    the ordinary answer: most files are nobody's original.
    """
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
    """Write down that this file was made from that one, and how.

    The row three separate things read: the copy's own page, which links back; the filter that
    finds every copy; and the check that stops the pair being offered as a duplicate somebody made
    by accident.

    `operation` is the verb somebody pressed rather than what ffmpeg was asked to do, which is why
    trimming and clipping are recorded apart when they are one command underneath. The person chose
    between two ideas and the record should say which they chose.

    **AND THE LEDGER'S `produced` EVENT, in the same transaction**: the ledger's verb for this
    act. The row above is not a record that outlives anything: it goes with the copy (`ON DELETE
    CASCADE`) and forgets its original (`SET NULL`) and its maker (`SET NULL`), so on its own "what
    was made from this, and by whom" is answerable only while all three still stand. The event names
    both files by the names they have now and the person who asked.

    THE SHAPE, for the reader: verb `produced`; the COPY as the subject; the ORIGINAL as the
    object; the user who asked as the actor (Sift, by the `produced` pass word, where no user is
    known); `{"operation": ...}` as the payload. Both files are named by the ledger's door from their
    rows: the copy was imported a moment ago and the original is what it was made from.
    """
    async with database.write() as connection:
        await connection.execute(
            _INSERT_PRODUCED,
            (new_id(), asset_id, source_asset_id, operation, preset, target_bytes, actor_id, now),
        )
        # The asker is looked up in this transaction, for the reason `kernel/jobs/ledger._finish`
        # gives: an encode takes minutes, the event's user is a foreign key, and a user removed in
        # the meantime would fail the whole write, the provenance row with it.
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
    """Where this file came from, for its own page. None when Sift did not make it.

    The original's name is resolved through the access layer, so a copy whose original the user
    may not see says it is a copy and does not say what of, rather than leaking a filename
    through a feature nobody thought of as a way to read one.
    """
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
