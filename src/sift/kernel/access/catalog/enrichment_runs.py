# SPDX-License-Identifier: AGPL-3.0-or-later
"""When a stash-box last enriched a thing, and what each ask filled in."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sift.kernel.access.catalog.makers import _BOX_NAMED, _TABLES
from sift.kernel.access.catalog.refusals import _KEPT_LOCAL
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.db import Connection, Database, point_read
from sift.kernel.ids import new_id

#: The last enrichment of one subject per box, newest first, sought on
#: `ix_enrichment_runs_subject`. The box is looked up apart, as in `made_by`, because `stash_boxes`
#: may be absent. Every ask is kept, and the id breaks a tie in `at`: two runs in one second are
#: ordinary, and a ULID minted under a floor that never goes down sorts the later one last.
_ENRICHMENT_OF = point_read(
    "catalog.enrichment_of",
    "SELECT box_id, at, automatic FROM enrichment_runs r"
    " WHERE r.subject = ? AND r.local_id = ?"
    " AND r.id = (SELECT x.id FROM enrichment_runs x"
    " WHERE x.subject = r.subject AND x.local_id = r.local_id AND x.box_id = r.box_id"
    " ORDER BY x.at DESC, x.id DESC LIMIT 1)"
    " ORDER BY at DESC, box_id",
)

#: Every ask is written, never replaced.
_RECORD_ENRICHMENT = (
    "INSERT INTO enrichment_runs (id, subject, local_id, box_id, at, automatic, applied)"
    " VALUES (?, ?, ?, ?, ?, ?, ?)"
)


@dataclass(frozen=True, slots=True)
class EnrichmentRun:
    """The last time one box enriched one thing, and whether anybody pressed it.

    The box's name and slug are carried because every reader draws them; a forgotten box has none.
    """

    box_id: str
    box_name: str | None
    box_slug: str | None
    at: int
    #: True where nobody pressed anything (Auto-enrich, or an exact match applying itself): the one
    #: fact about a run that cannot be recovered afterwards.
    automatic: bool
    #: The fields an ask filled are on the row too, read by the history threads directly.


async def enrichment_of(database: Database, kind: str, local_id: str) -> list[EnrichmentRun]:
    """Every box that has enriched this thing, newest first. Empty where none has, or for a kind
    this cannot describe."""
    if kind not in _KEPT_LOCAL or not local_id:
        return []
    rows = list(await database.fetch_all(_ENRICHMENT_OF, (kind, local_id)))
    if not rows:
        return []
    named: dict[str, tuple[str, str | None]] = {}
    here = {str(one["name"]) for one in await database.fetch_all(_TABLES)}
    if "stash_boxes" in here:
        for row in rows:
            found = await database.fetch_one(_BOX_NAMED, (str(row["box_id"]),))
            if found is not None:
                named[str(row["box_id"])] = (
                    str(found["name"]),
                    None if found["slug"] is None else str(found["slug"]),
                )
    return [
        EnrichmentRun(
            box_id=str(row["box_id"]),
            box_name=named.get(str(row["box_id"]), (None, None))[0],
            box_slug=named.get(str(row["box_id"]), (None, None))[1],
            at=int(row["at"]),
            automatic=bool(row["automatic"]),
        )
        for row in rows
    ]


def _applied(applied: Mapping[str, int] | Sequence[str]) -> dict[str, int] | list[str]:
    """What goes in the column: an object where the counts are known, a list where they are not."""
    return dict(applied) if isinstance(applied, Mapping) else list(applied)


async def record_enrichment(
    database: Database,
    kind: str,
    local_id: str,
    box_id: str,
    *,
    automatic: bool,
    at: int,
    applied: Mapping[str, int] | Sequence[str] | None = None,
) -> bool:
    """Write down that this box has just enriched this thing. False for a kind with no subject.

    Appends: a file's History asks how often a box was asked. `applied` is which fields the ask
    filled: None where no plan ran, an empty list where one ran and filled nothing, and a mapping of
    field to rows gained, stored as a JSON object (a bare sequence is still stored as a list).
    """
    if kind not in _KEPT_LOCAL or not local_id or not box_id:
        return False
    async with telling(database, EVERY_ADMIN, About.LIBRARY) as connection:
        return await record_enrichment_on(
            connection, kind, local_id, box_id, automatic=automatic, at=at, applied=applied
        )


async def record_enrichment_on(
    connection: Connection,
    kind: str,
    local_id: str,
    box_id: str,
    *,
    automatic: bool,
    at: int,
    applied: Mapping[str, int] | Sequence[str] | None = None,
) -> bool:
    """`record_enrichment` on the caller's connection, for a writer whose ledger event belongs in
    the same transaction as the run. False for a kind with no subject."""
    if kind not in _KEPT_LOCAL or not local_id or not box_id:
        return False
    await connection.execute(
        # `enrichment_runs.subject` has a CHECK naming the same four words the wire does.
        _RECORD_ENRICHMENT,
        (
            new_id(),
            kind,
            local_id,
            box_id,
            at,
            1 if automatic else 0,
            None if applied is None else json.dumps(_applied(applied)),
        ),
    )
    return True
