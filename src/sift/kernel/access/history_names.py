# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a thing an older event named is called now, where the row wrote no name down.

A name is a snapshot stored with the event (see `history_events.Thing`) and stays the answer where
there is one. Otherwise a thing still there is named live (`kernel.ledger.NAME_NOW`), one gone keeps
its kind, and a tunnel-valued setting's ids are said by the tunnels' names now. One read per kind.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

from sift.kernel.db import Database, in_clause
from sift.kernel.ledger import NAME_NOW
from sift.kernel.settings_registry import get_registered

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


def tunnel_ids(payload: Mapping[str, object]) -> list[object]:
    return [_decoded(payload.get("before")), _decoded(payload.get("after"))]


def tunnels_said_in(payload: Mapping[str, object], said: Mapping[str, str]) -> dict[str, object]:
    """The payload with each side said: a tunnel's name, or its menu's word for none."""
    declared = get_registered(str(payload.get("key")))
    none = declared.choice_labels[0] if declared and declared.choice_labels else None
    before, after = (
        said.get(one, none) if isinstance(one, str) and one else none for one in tunnel_ids(payload)
    )
    return {**payload, "before_said": before, "after_said": after}


def _decoded(stored: object) -> object:
    try:
        return json.loads(stored) if isinstance(stored, str) else None
    except ValueError:
        return None


def _unsaid_tunnel(one: LedgerEvent) -> dict[str, object] | None:
    if one.verb != "edited" or '"key"' not in one.payload:
        return None
    payload = _decoded(one.payload)
    if not isinstance(payload, dict) or "after_said" in payload:
        return None
    declared = get_registered(str(payload.get("key")))
    return payload if declared is not None and declared.names_a_tunnel else None


async def _tunnels_said(database: Database, events: list[LedgerEvent]) -> list[LedgerEvent]:
    from sift.kernel.tunnels.store import tunnels_said

    unsaid = {one.id: payload for one in events if (payload := _unsaid_tunnel(one)) is not None}
    if not unsaid:
        return events
    async with database.read() as connection:
        said = await tunnels_said(
            connection, [one for payload in unsaid.values() for one in tunnel_ids(payload)]
        )
    return [
        replace(one, payload=json.dumps(tunnels_said_in(unsaid[one.id], said)))
        if one.id in unsaid
        else one
        for one in events
    ]


async def objects_named(database: Database, events: Sequence[LedgerEvent]) -> list[LedgerEvent]:
    """The events, each nameless object named as it is now where it is still there."""
    events = await _tunnels_said(database, list(events))
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
