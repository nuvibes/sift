# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a thing an older event named is called now, where the row wrote no name down.

A name is a snapshot stored with the event (see `history_events.Thing`), and that stays the answer
wherever there is one. An older row has none and would read "a file" or "a tag", so a thing that is
still there is named live, from the ledger's own statements (`kernel.ledger.NAME_NOW`); one that
has gone keeps its kind. One read per kind on the page, and none for a page the door named.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

from sift.kernel.db import Database, in_clause
from sift.kernel.ledger import NAME_NOW

if TYPE_CHECKING:  # pragma: no cover
    from sift.kernel.access.history_events import LedgerEvent


async def names_now(
    database: Database, wanted: Mapping[str, Sequence[str]]
) -> dict[tuple[str, str], str]:
    """What these things are called now, by kind and id. A thing not found is left out."""
    named: dict[tuple[str, str], str] = {}
    for kind, ids in wanted.items():
        statement = NAME_NOW.get(kind)
        if statement is None or not ids:
            continue
        asked, values = in_clause(statement, sorted(set(ids)))
        for row in await database.fetch_all(asked, values):
            mapping = dict(row)
            if mapping["name"] is not None:
                named[(kind, str(mapping["id"]))] = str(mapping["name"])
    return named


async def objects_named(database: Database, events: Sequence[LedgerEvent]) -> list[LedgerEvent]:
    """The events, each nameless object named as it is now where it is still there."""
    nameless: dict[str, list[str]] = {}
    for one in events:
        if one.object is not None and one.object.name is None:
            nameless.setdefault(one.object.kind, []).append(one.object.id)
    now = await names_now(database, nameless) if nameless else {}
    return [
        replace(one, object=replace(one.object, name=now[(one.object.kind, one.object.id)]))
        if one.object is not None
        and one.object.name is None
        and (one.object.kind, one.object.id) in now
        else one
        for one in events
    ]
