# SPDX-License-Identifier: AGPL-3.0-or-later
"""A removal Sift made on a stash-box's answer, named for the box it was made on.

The event itself carries no box: the line names the box its take-back receipt names, else the
box whose kept answer named the thing, else the file's one applied answer. See `removals_named`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import replace
from typing import TYPE_CHECKING

from sift.kernel.access import sentences as say
from sift.kernel.access.history_boxes import (
    _BOX_TABLES,
    _FILES_PER_READ,
    _folded,
    stash_box_tables_in,
)
from sift.kernel.db import Database, Row, in_clause
from sift.kernel.vocabulary import VIA_STASH

if TYPE_CHECKING:
    from sift.kernel.access.history_events import LedgerEvent

#: The take-back receipts about these files: the line every take-back writes, naming the boxes.
_TAKE_BACKS_OF = """
SELECT s.subject_id AS asset_id, d.decided_at AS at, d.payload AS payload,
       d.object_name AS box
  FROM workbench_decision_subjects s
  JOIN workbench_decisions d ON d.id = s.decision_id
 WHERE s.kind = 'asset' AND s.subject_id IN (?*)
   AND d.verb = 'removed' AND d.object_kind = 'box'
"""

#: Each box's kept answer on these files, by the box's name, with whether it stands applied.
_ANSWERS_OF = """
SELECT m.asset_id AS asset_id, b.name AS box, m.state AS state, m.payload AS payload
  FROM asset_stash_box_matches m
  JOIN stash_boxes b ON b.id = m.box_id
 WHERE m.asset_id IN (?*)
"""

#: The other names of each thing a removal took off, which an answer may have named it by.
_ALIASES_OF: Mapping[str, str] = {
    "person": "SELECT person_id AS id, alias FROM people_aliases WHERE person_id IN (?*)",
    "tag": "SELECT tag_id AS id, alias FROM tag_aliases WHERE tag_id IN (?*)",
    "site": "SELECT site_id AS id, alias FROM site_aliases WHERE site_id IN (?*)",
}

#: How far apart a removal and its take-back's receipt may be stamped: one act, written in one
#: transaction from separate reads of the clock.
_SAME_ACT_SLACK = 2

#: Which of an answer's fields names a thing of each kind, read the way the take-back reads them.
_FIELD_OF_KIND: Mapping[str, str] = {"person": "people", "tag": "tags", "site": "site"}


def _answer_names(stored: object, kind: str) -> set[str]:
    """What one kept answer names of this kind, folded: its people, its tags or its Sites."""
    with suppress(TypeError, ValueError):
        held = json.loads(str(stored))
        records = held if isinstance(held, list) else [held]
        names: set[str] = set()
        for record in records:
            fields = record.get("fields") if isinstance(record, Mapping) else None
            if not isinstance(fields, Mapping):
                continue
            value = fields.get(_FIELD_OF_KIND.get(kind, ""))
            names |= _folded(value)
            if kind == "site":
                accounts = fields.get("accounts")
                for one in accounts if isinstance(accounts, list) else ():
                    if isinstance(one, Mapping):
                        names |= _folded(one.get("site"))
        return names
    return set()


def _receipt_boxes(row: Row, asset_id: str) -> list[str]:
    """The boxes one take-back receipt names for this file: its own list, else every box."""
    payload = say.payload_of(None if row["payload"] is None else str(row["payload"]))
    raw = payload.get("boxes")
    by_file = payload.get("by_file")
    if isinstance(by_file, Mapping) and isinstance(by_file.get(asset_id), list):
        raw = by_file[asset_id]
    if isinstance(raw, list) and raw:
        return [str(one) for one in raw if one]
    return [] if row["box"] is None else [str(row["box"])]


def _is_box_removal(event: LedgerEvent) -> bool:
    """A filing Sift took off a file on a stash-box's answer, read from either side."""
    return (
        event.verb == "unlinked"
        and event.actor_kind == "sift"
        and event.actor_id == VIA_STASH
        and event.object is not None
    )


async def removals_named(
    database: Database, drawn: Sequence[LedgerEvent], page_file: str | None = None
) -> list[LedgerEvent]:
    """These events, each filing Sift took off a file on a stash-box's answer naming that box.

    `page_file` is the file whose own page this is, where it is one: an event read from the file's
    side does not list the file among its subjects.

    The box goes into the payload under `sentences.ANSWER_OF`, which the line's back reads. An event
    that already names it keeps it. Otherwise, in order:

    - THE TAKE-BACK IT WAS PART OF: the receipt about the same file at the same moment names the
      boxes it took back. One box is that box.
    - Several: those whose kept answer on the file names the thing taken off, by its name or one
      of its other names.
    - No receipt: the file's one applied answer, the fallback `BOX_OF_A_ROW` keeps.

    Anything else names no box, and the line says "a stash-box", rather than a guess.
    """
    owed = [one for one in drawn if _is_box_removal(one) and _answer_of(one) is None]
    files = sorted({file for one in owed if (file := _file_of(one, page_file)) is not None})
    present = {str(row["name"]) for row in await database.fetch_all(_BOX_TABLES)}
    if not files or not stash_box_tables_in(present):
        return list(drawn)
    receipts: dict[str, list[Row]] = {}
    answers: dict[str, list[Row]] = {}
    for begin in range(0, len(files), _FILES_PER_READ):
        chunk = files[begin : begin + _FILES_PER_READ]
        query, params = in_clause(_TAKE_BACKS_OF, chunk)
        for row in await database.fetch_all(query, params):
            receipts.setdefault(str(row["asset_id"]), []).append(row)
        query, params = in_clause(_ANSWERS_OF, chunk)
        for row in await database.fetch_all(query, params):
            answers.setdefault(str(row["asset_id"]), []).append(row)
    aliases = await _aliases_of(database, owed)
    named: dict[str, list[str]] = {}
    for one in owed:
        file = _file_of(one, page_file)
        if file is None or one.object is None:
            continue
        boxes = _boxes_of_removal(
            one.at,
            one.object.kind,
            _folded(one.object.name) | aliases.get((one.object.kind, one.object.id), set()),
            receipts.get(file, []),
            answers.get(file, []),
            file,
        )
        if boxes:
            named[one.id] = boxes
    if not named:
        return list(drawn)
    return [
        replace(one, payload=_with_answer_of(one.payload, named[one.id]))
        if one.id in named
        else one
        for one in drawn
    ]


def _boxes_of_removal(
    at: int,
    kind: str,
    wanted: set[str],
    receipts: Sequence[Row],
    answers: Sequence[Row],
    file: str,
) -> list[str]:
    """The boxes one removal was made on, by the rules `removals_named` gives, or none."""
    same_act = [row for row in receipts if abs(int(row["at"]) - at) <= _SAME_ACT_SLACK]
    if same_act:
        boxes = list(dict.fromkeys(b for row in same_act for b in _receipt_boxes(row, file)))
        if len(boxes) == 1:
            return boxes
        said = {
            str(row["box"])
            for row in answers
            if str(row["box"]) in boxes and wanted & _answer_names(row["payload"], kind)
        }
        return [one for one in boxes if one in said]
    applied = sorted({str(row["box"]) for row in answers if row["state"] == "applied"})
    return applied if len(applied) == 1 else []


async def _aliases_of(
    database: Database, owed: Sequence[LedgerEvent]
) -> dict[tuple[str, str], set[str]]:
    """Each removed thing's other names, folded, by (kind, id). A table not here gives none."""
    present = {str(row["name"]) for row in await database.fetch_all(_ALIAS_TABLES)}
    out: dict[tuple[str, str], set[str]] = {}
    for kind, statement in _ALIASES_OF.items():
        ids = sorted({one.object.id for one in owed if one.object and one.object.kind == kind})
        if not ids or _ALIAS_TABLE[kind] not in present:
            continue
        for begin in range(0, len(ids), _FILES_PER_READ):
            query, params = in_clause(statement, ids[begin : begin + _FILES_PER_READ])
            for row in await database.fetch_all(query, params):
                out.setdefault((kind, str(row["id"])), set()).update(_folded(row["alias"]))
    return out


_ALIAS_TABLE: Mapping[str, str] = {
    "person": "people_aliases",
    "tag": "tag_aliases",
    "site": "site_aliases",
}
_ALIAS_TABLES = (
    "SELECT name FROM sqlite_master WHERE type = 'table'"
    " AND name IN ('people_aliases', 'tag_aliases', 'site_aliases')"
)


def _file_of(event: LedgerEvent, page_file: str | None) -> str | None:
    """The file a removal took the filing off: the one it names, else the page's own."""
    return next((one.id for one in event.subjects if one.kind == "asset"), page_file)


def _answer_of(event: LedgerEvent) -> object:
    """The boxes an event's payload already names (`sentences.ANSWER_OF`), or None."""
    return say.payload_of(event.payload).get(say.ANSWER_OF) or None


def _with_answer_of(stored: str | None, boxes: list[str]) -> str:
    """The payload again with the boxes named, keeping whatever the writer wrote beside it."""
    return json.dumps({**say.payload_of(stored), say.ANSWER_OF: boxes})
