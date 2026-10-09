# SPDX-License-Identifier: AGPL-3.0-or-later
"""The searches a person made, kept as their own box's memory and never as a query log."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode

from sift.kernel.db import Connection, register_schema_initializer
from sift.kernel.ids import is_id
from sift.kernel.log import get_logger
from sift.kernel.migrations import column_exists
from sift.slices.search.filter_parse import _as_field, _scan, quoted
from sift.slices.search.filters import Field
from sift.slices.search.stored import TYPED, entity_values, swapped

log = get_logger(__name__)

COMPONENT = "search_history"
VERSION = 11

# The box's memory: `subject` identifies the row (an id, or a value), `label` is only shown.
_CREATE_SEARCH_HISTORY = """
CREATE TABLE IF NOT EXISTS search_history (
  id         TEXT PRIMARY KEY,
  user_id    TEXT REFERENCES users(id) ON DELETE CASCADE,
  kind       TEXT NOT NULL,
  subject    TEXT NOT NULL,
  label      TEXT NOT NULL,
  created_at INTEGER NOT NULL
)
"""

_INDEXES = (
    # The only way this is ever read: one person's memory, most recent first.
    "CREATE INDEX IF NOT EXISTS ix_search_history_user ON search_history(user_id, created_at)",
    # A repeat moves the existing row; unique per kind, since a tag and a folder can share a name.
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_search_history_subject "
        "ON search_history(user_id, kind, subject)"
    ),
)

# Searches a person chose to keep under a name, unique per wall; scoped as history is.
_CREATE_SAVED_SEARCHES = """
CREATE TABLE IF NOT EXISTS saved_searches (
  id         TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind       TEXT NOT NULL DEFAULT 'asset',
  name       TEXT NOT NULL,
  query      TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  UNIQUE(user_id, kind, name)
)
"""

_SAVED_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_saved_searches_user ON saved_searches(user_id, created_at)",
)

#: Every search that was run, appended, kept per user for ever unless the user or install says.
_CREATE_SEARCH_EVENTS = """
CREATE TABLE IF NOT EXISTS search_events (
  id          TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL,
  subject     TEXT NOT NULL,
  results     INTEGER,
  opened_id   TEXT,
  at          INTEGER NOT NULL,
  device_id   TEXT,
  client_kind TEXT
)
"""

_ADD_CLIENT = (
    ("device_id", "ALTER TABLE search_events ADD COLUMN device_id TEXT"),
    ("client_kind", "ALTER TABLE search_events ADD COLUMN client_kind TEXT"),
)

#: A file opened from the wall a typed search narrowed; no key to `assets`, as a sitting has none.
_CREATE_SEARCH_OPENS = """
CREATE TABLE IF NOT EXISTS search_opens (
  id          TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  event_id    TEXT REFERENCES search_events(id) ON DELETE CASCADE,
  subject     TEXT NOT NULL,
  asset_id    TEXT NOT NULL,
  at          INTEGER NOT NULL,
  device_id   TEXT,
  client_kind TEXT
)
"""

_OPEN_INDEXES = (
    # One user's opens in time order, and an event's opens when it goes.
    "CREATE INDEX IF NOT EXISTS ix_search_opens_user ON search_opens(user_id, at)",
    "CREATE INDEX IF NOT EXISTS ix_search_opens_event ON search_opens(event_id)",
    # Forget in the box names a search by its words: its record is found by them.
    "CREATE INDEX IF NOT EXISTS ix_search_events_subject ON search_events(user_id, subject)",
)

_EVENT_INDEXES = (
    # One user's searches in time order, for the retention sweep and a recap alike.
    "CREATE INDEX IF NOT EXISTS ix_search_events_user ON search_events(user_id, at)",
)


#: Every name each entity field can be matched by, read only by the version 8 step.
_NAMES_OF: dict[Field, str] = {
    Field.TAGS: "SELECT id, name FROM tags",
    Field.SITES: "SELECT id, name FROM sites",
    Field.COLLECTIONS: "SELECT id, name FROM collections",
    Field.PHOTO_SETS: "SELECT id, name FROM photo_sets",
    Field.SONGS: "SELECT id, name FROM songs",
    # A one-time schema step reads every folder as the system, with no viewer to scope to.
    Field.IN: "SELECT id, name FROM folders UNION ALL SELECT id, rel_path AS name FROM folders",  # nosemgrep: sift-no-asset-sql-outside-kernel
    Field.PEOPLE: (
        "SELECT id, name FROM people"
        " UNION ALL SELECT person_id AS id, alias AS name FROM people_aliases"
        " UNION ALL SELECT person_id AS id, name FROM usernames WHERE person_id IS NOT NULL"
    ),
}

#: Every rename the record holds for things a filter names, oldest first.
_RENAMES = (
    "SELECT s.kind AS kind, s.subject_id AS id, d.payload AS payload"
    "  FROM workbench_decisions d"
    "  JOIN workbench_decision_subjects s ON s.decision_id = d.id"
    " WHERE d.verb = 'renamed'"
    "   AND s.kind IN ('tag', 'person', 'site', 'collection', 'photo_set', 'song')"
    " ORDER BY d.decided_at, d.id"
)
#: The names things were merged away under, and the survivor that took each in.
_MERGES = (
    "SELECT d.object_kind AS kind, d.object_id AS id, s.name AS before"
    "  FROM workbench_decisions d"
    "  JOIN workbench_decision_subjects s ON s.decision_id = d.id AND s.kind = d.object_kind"
    " WHERE d.verb = 'merged' AND d.object_id IS NOT NULL AND s.name IS NOT NULL"
    "   AND d.object_kind IN ('tag', 'person', 'site', 'collection', 'photo_set', 'song')"
    " ORDER BY d.decided_at, d.id"
)
_HAS_RECORD = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'workbench_decisions'"
_RENAMED_KIND: dict[str, Field] = {
    "tag": Field.TAGS,
    "person": Field.PEOPLE,
    "site": Field.SITES,
    "collection": Field.COLLECTIONS,
    "photo_set": Field.PHOTO_SETS,
    "song": Field.SONGS,
}

_KEPT_OVER_FILES = "SELECT id, query FROM saved_searches WHERE kind = 'asset'"
_REWRITE_KEPT = "UPDATE saved_searches SET query = ? WHERE id = ?"

#: OR IGNORE: a row already kept under that id is the same memory.
_PICKED_BY_NAME = "SELECT id, kind, subject FROM search_history WHERE kind IN ('tags', 'in')"
_REWRITE_PICKED = "UPDATE OR IGNORE search_history SET subject = ? WHERE id = ?"
_PICKED_FIELD = {"tags": Field.TAGS, "in": Field.IN}


def _folded(name: str) -> str:
    return name.strip().casefold()


async def _keep_by_id(connection: Connection) -> tuple[int, int]:
    """Every saved filter over files rewritten from names to ids where a name names one thing."""
    kept = list(await connection.execute_fetchall(_KEPT_OVER_FILES))
    picked = list(await connection.execute_fetchall(_PICKED_BY_NAME))
    if not kept and not picked:
        return 0, 0
    wanted = {str(row["id"]): entity_values(str(row["query"])) for row in kept}
    fields = {found for values in wanted.values() for found in values}
    fields |= {_PICKED_FIELD[str(row["kind"])] for row in picked}
    owners, renamed = await _owners(connection, fields)
    rewritten = left = 0
    for row in kept:
        swaps, missed = _swaps(wanted[str(row["id"])], owners, renamed)
        left += missed
        query = swapped(str(row["query"]), swaps)
        if query != row["query"]:
            await connection.execute(_REWRITE_KEPT, (query, row["id"]))
            rewritten += 1
    for row in picked:
        found = _PICKED_FIELD[str(row["kind"])]
        subject = str(row["subject"])
        one = _one_id(found, subject, owners, renamed)
        if not is_id(subject) and one is not None:
            await connection.execute(_REWRITE_PICKED, (one, row["id"]))
    return rewritten, left


Owners = dict[Field, dict[str, set[str]]]


async def _owners(connection: Connection, fields: set[Field]) -> tuple[Owners, Owners]:
    """Every name these fields can be matched by, and the names things were renamed away from."""
    owners: Owners = {}
    for found in sorted(fields):
        by_name: dict[str, set[str]] = {}
        for row in await connection.execute_fetchall(_NAMES_OF[found]):
            if row["name"]:
                by_name.setdefault(_folded(str(row["name"])), set()).add(str(row["id"]))
        owners[found] = by_name
    return owners, await _renamed_from(connection, owners)


def _one_id(found: Field, value: str, owners: Owners, renamed: Owners) -> str | None:
    """The one thing a name names across the library, or None where it names several or none."""
    # A folder is also named by its path, typed with or without a slash at the end.
    key = _folded(value).strip("/") if found is Field.IN else _folded(value)
    ids = owners[found].get(key, set()) or renamed.get(found, {}).get(key, set())
    return next(iter(ids)) if len(ids) == 1 else None


def _swaps(
    values: Mapping[Field, Mapping[str, set[str]]], owners: Owners, renamed: Owners
) -> tuple[dict[Field, dict[str, str]], int]:
    """What to put in place of each name in one kept filter, and how many names stay as they are."""
    swaps: dict[Field, dict[str, str]] = {}
    left = 0
    for found, named in values.items():
        for value in named:
            if is_id(value):
                continue
            one = _one_id(found, value, owners, renamed)
            if one is None:
                left += 1
            else:
                swaps.setdefault(found, {})[value] = one
    return swaps, left


#: The cells of saved Theater walls, brought forward by the version 9 step since the language is here.
_HAS_CELLS = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'theater_cells'"
_CELLS = "SELECT arrangement_id, position, source FROM theater_cells WHERE source <> ''"
_REWRITE_CELL = "UPDATE theater_cells SET source = ? WHERE arrangement_id = ? AND position = ?"


async def _cells_by_id(connection: Connection) -> tuple[int, int]:
    """Every saved Theater wall's cell, rewritten from names to ids where a name names one thing."""
    if not list(await connection.execute_fetchall(_HAS_CELLS)):
        return 0, 0
    cells = list(await connection.execute_fetchall(_CELLS))
    if not cells:
        return 0, 0
    wanted = [entity_values(urlencode([(TYPED, str(row["source"]))])) for row in cells]
    owners, renamed = await _owners(connection, {found for one in wanted for found in one})
    rewritten = left = 0
    for row, values in zip(cells, wanted, strict=True):
        swaps, missed = _swaps(values, owners, renamed)
        left += missed
        query = swapped(urlencode([(TYPED, str(row["source"]))]), swaps)
        source = dict(parse_qsl(query, keep_blank_values=True)).get(TYPED, "")
        if source != row["source"]:
            await connection.execute(
                _REWRITE_CELL, (source, row["arrangement_id"], row["position"])
            )
            rewritten += 1
    return rewritten, left


async def _renamed_from(
    connection: Connection, owners: Mapping[Field, Mapping[str, set[str]]]
) -> dict[Field, dict[str, set[str]]]:
    """The names things were renamed or merged away from, each with the one thing carrying it."""
    alive = {
        found: {key for ids in names.values() for key in ids} for found, names in owners.items()
    }
    if not list(await connection.execute_fetchall(_HAS_RECORD)):
        return {}
    followed: dict[Field, dict[str, set[str]]] = {}
    for row in await connection.execute_fetchall(_RENAMES):
        found = _RENAMED_KIND[str(row["kind"])]
        if found not in alive or str(row["id"]) not in alive[found]:
            continue
        try:
            before = json.loads(str(row["payload"] or "{}")).get("before")
        except (ValueError, AttributeError):
            continue
        if isinstance(before, str) and before.strip():
            followed.setdefault(found, {}).setdefault(_folded(before), set()).add(str(row["id"]))
    # A thing merged away points a filter naming it at the thing that took it in.
    for row in await connection.execute_fetchall(_MERGES):
        found = _RENAMED_KIND[str(row["kind"])]
        name = str(row["before"]).strip()
        if found in alive and str(row["id"]) in alive[found] and name:
            followed.setdefault(found, {}).setdefault(_folded(name), set()).add(str(row["id"]))
    return followed


_KEPT_TYPED = "SELECT id, query FROM saved_searches WHERE kind = 'asset' AND query LIKE '%q=%'"


def _named(found: Field, value: str, owners: Owners, renamed: Owners) -> bool:
    key = _folded(value).strip("/") if found is Field.IN else _folded(value)
    return bool(owners[found].get(key) or renamed.get(found, {}).get(key))


def _old_refusals(text: str) -> list[tuple[int, str, Field, str]]:
    """Each `field:"-X"` piece: where it starts, the piece, its field, and X."""
    out: list[tuple[int, str, Field, str]] = []
    for begin, piece in _scan(text):
        name, colon, value = piece.partition(":")
        found = _as_field(name) if colon and not piece.startswith("-") else None
        quoted_minus = value.startswith('"-') and value.endswith('"') and value.count('"') == 2
        if found is not None and found in _NAMES_OF and quoted_minus and len(value) > 3:
            out.append((begin, piece, found, value[2:-1]))
    return out


def _respelled(text: str, owners: Owners, renamed: Owners) -> tuple[str, list[str]]:
    """Typed text with each old refusal written `-field:X`, where `-X` names nothing and X does."""
    fields: list[str] = []
    for begin, piece, found, body in reversed(_old_refusals(text)):
        parts = re.split(r"([|,])", body)
        names = [one.strip() for one in parts[0::2]]
        if _named(found, f"-{body}", owners, renamed) or not all(
            one and _named(found, one, owners, renamed) for one in names
        ):
            continue
        parts[0::2] = [quoted(one) for one in names]
        written = f"-{piece.partition(':')[0]}:{''.join(parts)}"
        text = text[:begin] + written + text[begin + len(piece) :]
        fields.append(found.value)
    return text, fields


async def _refusals_respelled(connection: Connection) -> tuple[int, int]:
    """Every saved Theater cell and saved filter holding an old refusal, respelled; each logged."""
    cells: list[Any] = []
    if list(await connection.execute_fetchall(_HAS_CELLS)):
        cells = [row for row in await connection.execute_fetchall(_CELLS) if ':"-' in row["source"]]
    kept = [
        (row, dict(parse_qsl(str(row["query"]), keep_blank_values=True)).get(TYPED, ""))
        for row in await connection.execute_fetchall(_KEPT_TYPED)
    ]
    kept = [(row, typed) for row, typed in kept if ':"-' in typed]
    texts = [str(row["source"]) for row in cells] + [typed for _, typed in kept]
    candidates = [one for text in texts for one in _old_refusals(text)]
    if not candidates:
        return 0, 0
    owners, renamed = await _owners(connection, {found for _, _, found, _ in candidates})
    rewritten = respelled = 0
    for row in cells:
        source, fields = _respelled(str(row["source"]), owners, renamed)
        if fields:
            await connection.execute(
                _REWRITE_CELL, (source, row["arrangement_id"], row["position"])
            )
            log.info(
                "theater.cell.refusal_respelled",
                arrangement=row["arrangement_id"],
                position=row["position"],
                fields=fields,
            )
            rewritten, respelled = rewritten + 1, respelled + len(fields)
    for row, typed in kept:
        text, fields = _respelled(typed, owners, renamed)
        if fields:
            pairs = parse_qsl(str(row["query"]), keep_blank_values=True)
            query = urlencode([(name, text if name == TYPED else value) for name, value in pairs])
            await connection.execute(_REWRITE_KEPT, (query, row["id"]))
            log.info("search.saved.refusal_respelled", saved=row["id"], fields=fields)
            rewritten, respelled = rewritten + 1, respelled + len(fields)
    return rewritten, len(candidates) - respelled


async def initialize(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        for statement in (
            _CREATE_SEARCH_HISTORY,
            *_INDEXES,
            _CREATE_SAVED_SEARCHES,
            *_SAVED_INDEXES,
            _CREATE_SEARCH_EVENTS,
            *_EVENT_INDEXES,
        ):
            await connection.execute(statement)
    if on_disk < 8:
        rewritten, left = await _keep_by_id(connection)
        log.info("search.saved.kept_by_id", rewritten=rewritten, names_left=left)
    if on_disk < 9:
        rewritten, left = await _cells_by_id(connection)
        log.info("theater.cells.kept_by_id", rewritten=rewritten, names_left=left)
    if on_disk < 10:
        # Each column only where it is missing: a new library made them in the table above.
        for column, statement in _ADD_CLIENT:
            if not await column_exists(connection, "search_events", column):
                await connection.execute(statement)
        for statement in (_CREATE_SEARCH_OPENS, *_OPEN_INDEXES):
            await connection.execute(statement)
    if 0 < on_disk < 11:
        rewritten, left = await _refusals_respelled(connection)
        log.info("search.refusals_respelled", rewritten=rewritten, left=left)


# The record of renames belongs to a slice this component must not require, so it is asked for.
register_schema_initializer(
    COMPONENT,
    VERSION,
    initialize,
    depends_on=["identity", "catalog", "content"],
    baseline=7,
)
