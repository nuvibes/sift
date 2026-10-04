# SPDX-License-Identifier: AGPL-3.0-or-later
"""One table: the searches a person made, so their own box can offer them back.

This is a feature, and the distinction from a log is the entire reason it is allowed to exist.

A query log would be a record of exactly what somebody went looking for, kept for the operator's
benefit, and it is among the most revealing things this application could write down. Nothing here
writes one: no search reaches the structured log, not the text, not the terms, not a count of
them.

What this table is instead: the dropdown's memory, kept per user, shown only to the user it
belongs to, and clearable by them from the same dropdown. It is theirs. It goes when the user
goes, and it goes sooner if they say so.

Two consequences follow from that framing and are enforced rather than assumed. It is keyed on the
user and every read of it is filtered by the asking viewer, so one user's history is never a
window into another's. And the list is capped per user, because a memory that grows without
bound stops being a convenience and becomes the log this is not.

The search INDEX is not here. `assets_fts` lives in the kernel, beside the tables the permission
resolver joins, because free text has to filter the scoped read from inside the one statement that
also decides visibility (see the note above its DDL). This slice owns what is done with it: the
language, the reindex job, and the endpoints.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from urllib.parse import parse_qsl, urlencode

from sift.kernel.db import Connection, register_schema_initializer
from sift.kernel.ids import is_id
from sift.kernel.log import get_logger
from sift.kernel.migrations import column_exists
from sift.slices.search.filters import Field
from sift.slices.search.stored import TYPED, entity_values, swapped

log = get_logger(__name__)

COMPONENT = "search_history"
VERSION = 10

# A row of the box's memory: the queries somebody typed and ran, and the people, tags, Sites,
# collections, folders and files picked straight out of the dropdown.
#
# `subject` is what identifies the row: the query as typed, or, for something that was picked,
# whatever taking somebody back to it needs. That is the id where the thing has one and the value
# where the thing IS a value, such as a folder's path. It is deliberately ONE column: the choice
# between an id and a value belongs to the kind, and splitting it would let a row carry both and
# leave the reader deciding which it meant.
#
# `label` is what the row shows and nothing else reads it. A name is not an identity (two people
# can share one and names are edited), so it is stored beside the subject rather than instead of
# it, and a rename shows up the next time the thing is picked.
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
    # Doing the same thing again moves the existing row rather than adding a second one, which is
    # what keeps the dropdown from filling with one query typed five times or one person opened
    # every day. The uniqueness is what the upsert conflicts on, so it is a constraint and not
    # merely an index, and it is on the KIND as well, because a tag and a folder can share a name
    # and they are not the same row.
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_search_history_subject "
        "ON search_history(user_id, kind, subject)"
    ),
)

# The searches a person chose to KEEP, under a name they gave them.
#
# The sibling of history and the deliberate opposite of it. History is automatic and forgettable:
# what you happened to type, offered back and cleared without a thought. A saved search is a
# decision: this query is worth a name and a place to come back to. So it is named, it is not
# trimmed to a cap, and it goes only when the person deletes it or the user does.
#
# Keyed and scoped exactly as history is, and for the same reason: it is theirs, read only by them,
# gone when they go. A query is just text; applying it runs through the same filter engine as any
# typed search, which enforces visibility, so a saved search grants nothing a typed one would not,
# and needs no ACL of its own.
#
# `kind` is which wall the filter is a question about: a filter kept on the wall of files is spelled
# in the query language, one kept on the People wall in that noun's own facets. The name is unique
# per wall, not per user, because "Long ones" on People and "Long ones" on the library are two
# filters; saving under a name already used on that wall replaces its query, which is what the
# upsert conflicts on.
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

#: Every search that was RUN, appended, never replaced.
#:
#: The memory above is a recents list by design and has to stay one: one row per query, unique,
#: trimmed to fifty, because it is what the box offers back. That shape can never answer "how
#: often", "did it find anything" or "what did I open": the upsert destroys the previous time, and
#: the trim destroys the fifty-first query outright. This is the record the recents list makes
#: impossible.
#:
#: The same privacy framing as the memory beside it: it is keyed on the user, it is only ever read
#: back to that user, it goes when the user goes, and no search reaches the structured log. KEPT FOR
#: EVER BY DEFAULT, as sittings are, because the questions it answers are about years ("you searched
#: more than last year", the first thing you ever looked for) and a year's horizon loses the first
#: year the day the second one ends. A horizon is the install's to set (`search_events_prune`, set on
#: Privacy), Forget in the search box takes a search's record with it, and a User can clear theirs
#: whole. It is not a query log: nothing of it reaches the structured log, it is read back to nobody
#: but its User, and its User can empty it.
#:
#: `results` is how many the search found, or NULL where the caller did not say. `opened_id` is what
#: was opened straight out of the dropdown. A file opened from the wall a search narrowed is in
#: `search_opens`, since one search can lead to several. `device_id` and `client_kind` are the client
#: the search was made from (`kernel/client.py`), NULL on a search made before version 10.
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

#: A file opened from the wall a typed search narrowed: which file, from which search. `event_id` is
#: the latest record of that search by this User, and goes with it (forgotten, cleared, past the
#: horizon); `subject` is the words, kept beside it so the open still says what was searched where
#: the record was never written (the history paused, an address pasted in). No key to `assets`:
#: what somebody opened stays true after the file has gone, as a sitting does.
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
    # One user's searches in time order, which is both readings this table has: the retention
    # sweep walks the old end of it and a recap walks a year of one user.
    "CREATE INDEX IF NOT EXISTS ix_search_events_user ON search_events(user_id, at)",
)


#: Every name each entity field can be matched by, with the id it belongs to. Fixed statements, one
#: per field, read only by the version 8 step below: the same sources the live resolvers match (a
#: person by their name, an alias or a linked username; a folder by its name or its path).
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

#: Every rename the record holds for the things a filter names, oldest first: the id, what kind of
#: thing it is and (in the payload) the name it had before. What lets the step follow a filter
#: kept by a name the thing has since given up, which is the one case a name lookup cannot answer.
_RENAMES = (
    "SELECT s.kind AS kind, s.subject_id AS id, d.payload AS payload"
    "  FROM workbench_decisions d"
    "  JOIN workbench_decision_subjects s ON s.decision_id = d.id"
    " WHERE d.verb = 'renamed'"
    "   AND s.kind IN ('tag', 'person', 'site', 'collection', 'photo_set', 'song')"
    " ORDER BY d.decided_at, d.id"
)
#: The names things were MERGED away under: each thing merged into another, by the name the merge
#: recorded for it, and the thing that took it in. The survivor keeps the merged one's files and
#: its name as an alias, so a filter naming the merged thing names the survivor.
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

#: The box's memory of a tag or a folder picked by name, which the service now keeps by id. OR
#: IGNORE: a row already kept under that id since is the same memory, and the older one keeps its
#: name and reads as it always did.
_PICKED_BY_NAME = "SELECT id, kind, subject FROM search_history WHERE kind IN ('tags', 'in')"
_REWRITE_PICKED = "UPDATE OR IGNORE search_history SET subject = ? WHERE id = ?"
_PICKED_FIELD = {"tags": Field.TAGS, "in": Field.IN}


def _folded(name: str) -> str:
    return name.strip().casefold()


async def _keep_by_id(connection: Connection) -> tuple[int, int]:
    """Every saved filter over files, rewritten from names to ids where a name names one thing,
    and the box's memory of a tag or a folder picked by name, the same way.

    The one-time half of keeping filters by id (see `stored`): a filter saved from now on is kept
    by id as it is written, and this brings the ones kept before up to the same form. A name
    that names exactly one thing in the library becomes its id. A name that names several stays,
    because it has always meant all of them. A name that names nothing is looked for in the
    record of renames: a thing renamed BEFORE this step ran gave its old name up, and the record
    says which thing had it (see `_renamed_from`). A name neither answers stays, and reads on its
    chip as a name nothing answers to any more.

    Resolved across the whole library rather than per viewer, because a schema step has no
    viewer; the id it writes is checked against what the viewer may see every time the filter is
    compiled, so an id they may not see matches nothing, as the name did for them.

    Answers how many filters were rewritten and how many names were left as they were.
    """
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
    """Every name each of these fields can be matched by, with the ids carrying it, and the names
    things were renamed away from (see `_renamed_from`). Read once for a whole step."""
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


#: The cells of every saved Theater wall, which keep a filter as typed text (`tags:harbour`). The
#: table belongs to the Theater slice, whose cells are kept by id through the filter engine from
#: the moment they are saved (`FilterEngine.kept`); the cells saved before that are brought to the
#: same form by the version 9 step below, here, because the language is this slice's and a step
#: in the Theater slice could not read it. Asked whether it is there, for the reason the record of
#: renames is: a database built without that slice has no cells to bring forward.
_HAS_CELLS = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'theater_cells'"
_CELLS = "SELECT arrangement_id, position, source FROM theater_cells WHERE source <> ''"
_REWRITE_CELL = "UPDATE theater_cells SET source = ? WHERE arrangement_id = ? AND position = ?"


async def _cells_by_id(connection: Connection) -> tuple[int, int]:
    """Every saved Theater wall's cell, rewritten from names to ids where a name names one thing.

    The same rule as `_keep_by_id`, over typed text rather than an address: resolved across the
    whole library, following the record of renames, and a name that names several things or
    nothing left as it was typed. Answers how many cells were rewritten and how many names stayed.
    """
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
    """The names things were renamed or merged AWAY from, each with the one thing that carried it.

    A filter kept as `tags=harbour` after `harbour` is renamed `quayside` names nothing by that name,
    and without this it could only be shown as gone. The record says which tag was called `harbour`
    before, so the filter is given that tag's id and reads `quayside` from then on. Only a thing
    that still exists is taken, and only where the old name leads to exactly one of them.
    """
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
    # A thing merged into another is gone by its own name, and the record says which one took it
    # in: a Site merged away leaves a filter naming it pointing at the Site that holds its files.
    for row in await connection.execute_fetchall(_MERGES):
        found = _RENAMED_KIND[str(row["kind"])]
        name = str(row["before"]).strip()
        if found in alive and str(row["id"]) in alive[found] and name:
            followed.setdefault(found, {}).setdefault(_folded(name), set()).add(str(row["id"]))
    return followed


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


# `users` comes from the identity component, so the key names a table that exists by the time this
# runs. The catalog and the content components keep the tables a saved filter names things in
# (tags, people, Sites, collections, Photo Sets, folders), which the version 8 step reads. The record
# of renames it also reads belongs to a slice this component must not require (a database built for
# one feature's tests has no record at all), so the step asks whether that table is there instead.
register_schema_initializer(
    COMPONENT,
    VERSION,
    initialize,
    depends_on=["identity", "catalog", "content"],
    baseline=7,
)
