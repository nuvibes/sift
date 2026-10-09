# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading the ledger back, each read filtered through the stored visibility verdict."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final

from sift.kernel.access.history_names import names_now as names_now
from sift.kernel.access.history_names import objects_named
from sift.kernel.access.viewer import Viewer
from sift.kernel.db import Database, Row, in_clause
from sift.kernel.sql_splice import splice
from sift.kernel.vocabulary import LEDGER_QUEUE, SubjectKind

DEFAULT_LIMIT = 200

#: A cap, because this is the one table that grows with every press anybody makes.
MAX_LIMIT = 1000


@dataclass(frozen=True, slots=True)
class Thing:
    """A row an event names, with its name then: a snapshot, since an event outlives it."""

    kind: str
    id: str
    name: str | None = None


@dataclass(frozen=True, slots=True)
class LedgerEvent:
    """One row of the ledger, as it was stored."""

    id: str
    at: int
    verb: str | None
    actor_kind: str | None
    actor_id: str | None
    #: NULL once that user is deleted, which is why `actor_id` is stored beside it.
    user_id: str | None
    object: Thing | None
    #: None on an ordinary event; a pass over a single file writes the file.
    count: int | None
    queue: str
    #: The writer's own notes on its act; read on the server to build a sentence, never sent.
    payload: str
    title: str
    detail: str
    #: A reversed event keeps its place, so a history never hides its reversals.
    reversed_at: int | None
    subjects: tuple[Thing, ...] = ()


#: The vault's answer, spliced into every read: no file the event names is one the viewer may not
#: see. A deleted subject is cleared for an admin only.
NOTHING_HIDDEN = """
   AND NOT EXISTS (
         SELECT 1
           FROM workbench_decision_subjects hidden
          WHERE hidden.decision_id = d.id
            AND hidden.kind = 'asset'
            AND NOT EXISTS (SELECT 1 FROM viewer_assets v
                             WHERE v.user_id = :viewer AND v.asset_id = hidden.subject_id
                               AND (:reveal = 1 OR v.concealed = 0))
            AND NOT (:admin = 1
                     AND NOT EXISTS (SELECT 1 FROM assets gone WHERE gone.id = hidden.subject_id)))
   AND (d.object_kind IS NULL
        OR d.object_kind <> 'asset'
        OR EXISTS (SELECT 1 FROM viewer_assets vo
                    WHERE vo.user_id = :viewer AND vo.asset_id = d.object_id
                      AND (:reveal = 1 OR vo.concealed = 0))
        OR (:admin = 1
            AND NOT EXISTS (SELECT 1 FROM assets ogone WHERE ogone.id = d.object_id)))
   AND (:reveal = 1 OR NOT EXISTS (
         SELECT 1
           FROM workbench_decision_subjects named
          WHERE named.decision_id = d.id
            AND ((named.kind = 'person' AND (
                 EXISTS (SELECT 1 FROM person_user_state hs
                          WHERE hs.person_id = named.subject_id AND hs.user_id = :viewer AND hs.hidden = 1)
              OR EXISTS (SELECT 1 FROM viewer_entity_counts hc
                          WHERE hc.user_id = :viewer AND hc.kind = 'person' AND hc.object_id = named.subject_id
                            AND hc.permitted <= hc.concealed)))
              OR (named.kind = 'tag' AND (
                 EXISTS (SELECT 1 FROM tag_user_state hs
                          WHERE hs.tag_id = named.subject_id AND hs.user_id = :viewer AND hs.hidden = 1)
              OR EXISTS (SELECT 1 FROM viewer_entity_counts hc
                          WHERE hc.user_id = :viewer AND hc.kind = 'tag' AND hc.object_id = named.subject_id
                            AND hc.permitted <= hc.concealed)))
              OR (named.kind = 'collection' AND (
                 EXISTS (SELECT 1 FROM collection_user_state hs
                          WHERE hs.collection_id = named.subject_id AND hs.user_id = :viewer AND hs.hidden = 1)
              OR EXISTS (SELECT 1 FROM viewer_entity_counts hc
                          WHERE hc.user_id = :viewer AND hc.kind = 'collection' AND hc.object_id = named.subject_id
                            AND hc.permitted <= hc.concealed)))
              OR (named.kind = 'site' AND (
                 EXISTS (SELECT 1 FROM site_user_state hs
                          WHERE hs.site_id = named.subject_id AND hs.user_id = :viewer AND hs.hidden = 1)
              OR EXISTS (SELECT 1 FROM viewer_entity_counts hc
                          WHERE hc.user_id = :viewer AND hc.kind = 'site' AND hc.object_id = named.subject_id
                            AND hc.permitted <= hc.concealed)))
              OR (named.kind = 'photo_set' AND (
                 EXISTS (SELECT 1 FROM photo_set_user_state hs
                          WHERE hs.photo_set_id = named.subject_id AND hs.user_id = :viewer AND hs.hidden = 1)
              OR EXISTS (SELECT 1 FROM viewer_entity_counts hc
                          WHERE hc.user_id = :viewer AND hc.kind = 'photo_set' AND hc.object_id = named.subject_id
                            AND hc.permitted <= hc.concealed)))
              OR (named.kind = 'song' AND (
                 EXISTS (SELECT 1 FROM song_user_state hs
                          WHERE hs.song_id = named.subject_id AND hs.user_id = :viewer AND hs.hidden = 1)
              OR EXISTS (SELECT 1 FROM viewer_entity_counts hc
                          WHERE hc.user_id = :viewer AND hc.kind = 'song' AND hc.object_id = named.subject_id
                            AND hc.permitted <= hc.concealed))))))
   AND (:reveal = 1 OR d.object_kind IS NULL
        OR NOT ((d.object_kind = 'person' AND (
                 EXISTS (SELECT 1 FROM person_user_state hs
                          WHERE hs.person_id = d.object_id AND hs.user_id = :viewer AND hs.hidden = 1)
              OR EXISTS (SELECT 1 FROM viewer_entity_counts hc
                          WHERE hc.user_id = :viewer AND hc.kind = 'person' AND hc.object_id = d.object_id
                            AND hc.permitted <= hc.concealed)))
              OR (d.object_kind = 'tag' AND (
                 EXISTS (SELECT 1 FROM tag_user_state hs
                          WHERE hs.tag_id = d.object_id AND hs.user_id = :viewer AND hs.hidden = 1)
              OR EXISTS (SELECT 1 FROM viewer_entity_counts hc
                          WHERE hc.user_id = :viewer AND hc.kind = 'tag' AND hc.object_id = d.object_id
                            AND hc.permitted <= hc.concealed)))
              OR (d.object_kind = 'collection' AND (
                 EXISTS (SELECT 1 FROM collection_user_state hs
                          WHERE hs.collection_id = d.object_id AND hs.user_id = :viewer AND hs.hidden = 1)
              OR EXISTS (SELECT 1 FROM viewer_entity_counts hc
                          WHERE hc.user_id = :viewer AND hc.kind = 'collection' AND hc.object_id = d.object_id
                            AND hc.permitted <= hc.concealed)))
              OR (d.object_kind = 'site' AND (
                 EXISTS (SELECT 1 FROM site_user_state hs
                          WHERE hs.site_id = d.object_id AND hs.user_id = :viewer AND hs.hidden = 1)
              OR EXISTS (SELECT 1 FROM viewer_entity_counts hc
                          WHERE hc.user_id = :viewer AND hc.kind = 'site' AND hc.object_id = d.object_id
                            AND hc.permitted <= hc.concealed)))
              OR (d.object_kind = 'photo_set' AND (
                 EXISTS (SELECT 1 FROM photo_set_user_state hs
                          WHERE hs.photo_set_id = d.object_id AND hs.user_id = :viewer AND hs.hidden = 1)
              OR EXISTS (SELECT 1 FROM viewer_entity_counts hc
                          WHERE hc.user_id = :viewer AND hc.kind = 'photo_set' AND hc.object_id = d.object_id
                            AND hc.permitted <= hc.concealed)))
              OR (d.object_kind = 'song' AND (
                 EXISTS (SELECT 1 FROM song_user_state hs
                          WHERE hs.song_id = d.object_id AND hs.user_id = :viewer AND hs.hidden = 1)
              OR EXISTS (SELECT 1 FROM viewer_entity_counts hc
                          WHERE hc.user_id = :viewer AND hc.kind = 'song' AND hc.object_id = d.object_id
                            AND hc.permitted <= hc.concealed)))))
"""

_EVENT_COLUMNS = """
SELECT d.id AS id, d.decided_at AS at, d.verb AS verb,
       d.actor_kind AS actor_kind, d.actor_id AS actor_id, d.user_id AS user_id,
       d.object_kind AS object_kind, d.object_id AS object_id, d.object_name AS object_name,
       d.touched AS touched, d.queue AS queue, d.payload AS payload, d.title AS title,
       d.detail AS detail, d.reversed_at AS reversed_at
"""

# Everything that happened to one file, as subject or object. A UNION of two seeks, because an OR
# here gives SQLite nothing to seek.
_OF_ASSET = splice(
    """
{{COLUMNS}}
  FROM workbench_decision_subjects s
  JOIN workbench_decisions d ON d.id = s.decision_id
 WHERE s.kind = 'asset' AND s.subject_id = :subject
{{NOTHING_HIDDEN}}
UNION
{{COLUMNS}}
  FROM workbench_decisions d
 WHERE d.object_kind = 'asset' AND d.object_id = :subject
{{NOTHING_HIDDEN}}
 ORDER BY at DESC, id DESC
 LIMIT :limit
""",
    COLUMNS=_EVENT_COLUMNS,
    NOTHING_HIDDEN=NOTHING_HIDDEN,
)

# How one file arrived: its own seek, since the capped page would lose the oldest events.
_ARRIVAL = splice(
    """
{{COLUMNS}}
  FROM workbench_decision_subjects s
  JOIN workbench_decisions d ON d.id = s.decision_id
 WHERE s.kind = 'asset' AND s.subject_id = :subject
   AND d.verb = 'added' AND d.actor_kind = 'sift' AND d.queue = :ledger
{{NOTHING_HIDDEN}}
 ORDER BY d.id DESC
 LIMIT 1
""",
    COLUMNS=_EVENT_COLUMNS,
    NOTHING_HIDDEN=NOTHING_HIDDEN,
)

# Every press of a pass over one file; its own seek for the same reason.
_PRESSES_OF_ASSET = splice(
    """
{{COLUMNS}}
  FROM workbench_decision_subjects s
  JOIN workbench_decisions d ON d.id = s.decision_id
 WHERE s.kind = 'asset' AND s.subject_id = :subject
   AND d.verb = 'pressed' AND d.actor_kind = 'user'
{{NOTHING_HIDDEN}}
 ORDER BY d.decided_at DESC, d.id DESC
 LIMIT :limit
""",
    COLUMNS=_EVENT_COLUMNS,
    NOTHING_HIDDEN=NOTHING_HIDDEN,
)

# The same question about any entity, its kind bound so one statement serves every page.
_OF_ENTITY = splice(
    """
{{COLUMNS}}
  FROM workbench_decision_subjects s
  JOIN workbench_decisions d ON d.id = s.decision_id
 WHERE s.kind = :kind AND s.subject_id = :subject
{{NOTHING_HIDDEN}}
UNION
{{COLUMNS}}
  FROM workbench_decisions d
 WHERE d.object_kind = :kind AND d.object_id = :subject
{{NOTHING_HIDDEN}}
 ORDER BY at DESC, id DESC
 LIMIT :limit
""",
    COLUMNS=_EVENT_COLUMNS,
    NOTHING_HIDDEN=NOTHING_HIDDEN,
)

_LATEST_OF_ENTITIES = splice(
    """
SELECT * FROM (
  SELECT named.*,
         ROW_NUMBER() OVER (PARTITION BY named.entity ORDER BY named.at DESC, named.id DESC) AS nth
    FROM (
{{COLUMNS}}, s.subject_id AS entity
  FROM workbench_decision_subjects s
  JOIN workbench_decisions d ON d.id = s.decision_id
 WHERE s.kind = :kind AND s.subject_id IN (SELECT value FROM json_each(:subjects))
{{NOTHING_HIDDEN}}
UNION
{{COLUMNS}}, d.object_id AS entity
  FROM workbench_decisions d
 WHERE d.object_kind = :kind AND d.object_id IN (SELECT value FROM json_each(:subjects))
{{NOTHING_HIDDEN}}
    ) named
)
 WHERE nth = 1
""",
    COLUMNS=_EVENT_COLUMNS,
    NOTHING_HIDDEN=NOTHING_HIDDEN,
)

#: "Not given OR matches", so one statement answers the filtered and unfiltered question without SQL
#: built at run time.
_FILTERED = """
   AND (:verb IS NULL OR d.verb = :verb)
   AND (:decisions = 0 OR d.queue <> :ledger)
   AND (:kind IS NULL
        OR EXISTS (SELECT 1 FROM workbench_decision_subjects narrowed
                    WHERE narrowed.decision_id = d.id AND narrowed.kind = :kind))
"""

# The whole install, newest first; it resolves no subject, so the filter above is the only gate.
_RECENT = splice(
    """
{{COLUMNS}}
  FROM workbench_decisions d
 WHERE 1 = 1
{{NOTHING_HIDDEN}}
{{FILTERED}}
 ORDER BY d.decided_at DESC, d.id DESC
 LIMIT :limit OFFSET :offset
""",
    COLUMNS=_EVENT_COLUMNS,
    NOTHING_HIDDEN=NOTHING_HIDDEN,
    FILTERED=_FILTERED,
)

_SUBJECTS_OF = (
    "SELECT decision_id, kind, subject_id, name FROM workbench_decision_subjects"
    " WHERE decision_id IN (?*)"
)


def verdict_of(viewer: Viewer) -> dict[str, object]:
    """The three values the vault's answer binds, from the session asking."""
    return {
        "viewer": viewer.id,
        "reveal": 1 if viewer.show_hidden else 0,
        "admin": 1 if viewer.is_admin else 0,
    }


def _event(row: Row) -> LedgerEvent:
    mapping = dict(row)
    object_kind = mapping["object_kind"]
    return LedgerEvent(
        id=str(mapping["id"]),
        at=int(mapping["at"]),
        verb=None if mapping["verb"] is None else str(mapping["verb"]),
        actor_kind=None if mapping["actor_kind"] is None else str(mapping["actor_kind"]),
        actor_id=None if mapping["actor_id"] is None else str(mapping["actor_id"]),
        user_id=None if mapping["user_id"] is None else str(mapping["user_id"]),
        object=None
        if object_kind is None
        else Thing(
            kind=str(object_kind),
            id=str(mapping["object_id"]),
            name=None if mapping["object_name"] is None else str(mapping["object_name"]),
        ),
        count=None if mapping["touched"] is None else int(mapping["touched"]),
        queue=str(mapping["queue"]),
        payload=str(mapping["payload"] or ""),
        title=str(mapping["title"]),
        detail=str(mapping["detail"]),
        reversed_at=None if mapping["reversed_at"] is None else int(mapping["reversed_at"]),
    )


def _kept(limit: int) -> int:
    return max(1, min(limit, MAX_LIMIT))


async def events_of_asset(
    database: Database, viewer: Viewer, asset_id: str, *, limit: int = DEFAULT_LIMIT
) -> list[LedgerEvent]:
    """Every event that named one file, newest first; scoped since an event can name others."""
    rows = await database.fetch_all(
        _OF_ASSET, {**verdict_of(viewer), "subject": asset_id, "limit": _kept(limit)}
    )
    return await objects_named(database, [_event(row) for row in rows])


async def arrival_of(database: Database, viewer: Viewer, asset_id: str) -> LedgerEvent | None:
    """How one file arrived, where Sift recorded it, as this user may be told; else None."""
    row = await database.fetch_one(
        _ARRIVAL, {**verdict_of(viewer), "subject": asset_id, "ledger": LEDGER_QUEUE}
    )
    return None if row is None else _event(row)


async def presses_of_asset(
    database: Database, viewer: Viewer, asset_id: str, *, limit: int = MAX_LIMIT
) -> list[LedgerEvent]:
    """Every press of a pass over one file, newest first, as this user may be told about it."""
    rows = await database.fetch_all(
        _PRESSES_OF_ASSET, {**verdict_of(viewer), "subject": asset_id, "limit": _kept(limit)}
    )
    return [_event(row) for row in rows]


async def events_of_entity(
    database: Database,
    viewer: Viewer,
    kind: SubjectKind,
    entity_id: str,
    *,
    limit: int = DEFAULT_LIMIT,
) -> list[LedgerEvent]:
    """Every event that named one person, site, tag, collection, Photo Set or folder."""
    rows = await database.fetch_all(
        _OF_ENTITY,
        {**verdict_of(viewer), "kind": kind, "subject": entity_id, "limit": _kept(limit)},
    )
    return await objects_named(database, [_event(row) for row in rows])


async def latest_events_of_entities(
    database: Database, viewer: Viewer, kind: SubjectKind, entity_ids: Sequence[str]
) -> dict[str, LedgerEvent]:
    """The newest event naming each of these things, by id; one with none is absent."""
    if not entity_ids:
        return {}
    rows = await database.fetch_all(
        _LATEST_OF_ENTITIES,
        {**verdict_of(viewer), "kind": kind, "subjects": json.dumps(sorted(set(entity_ids)))},
    )
    named = await objects_named(database, [_event(row) for row in rows])
    return {str(row["entity"]): event for row, event in zip(rows, named, strict=True)}


async def events_recent(
    database: Database,
    viewer: Viewer,
    *,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    kind: str | None = None,
    verb: str | None = None,
    decisions: bool = False,
) -> list[LedgerEvent]:
    """What this installation has been doing, newest first, with what each event was about."""
    rows = await database.fetch_all(
        _RECENT,
        {
            **verdict_of(viewer),
            "limit": _kept(limit),
            "offset": max(0, offset),
            "kind": kind,
            "verb": verb,
            "decisions": int(decisions),
            "ledger": LEDGER_QUEUE,
        },
    )
    events = await objects_named(database, [_event(row) for row in rows])
    named = await subjects_of(database, [one.id for one in events]) if events else {}
    return [replace(one, subjects=tuple(named.get(one.id, ()))) for one in events]


async def subjects_of(database: Database, event_ids: Sequence[str]) -> dict[str, list[Thing]]:
    """What each of these events was about, asked once for the whole page."""
    if not event_ids:
        return {}
    statement, values = in_clause(_SUBJECTS_OF, sorted(set(event_ids)))
    rows = [dict(row) for row in await database.fetch_all(statement, values)]
    # A subject with no snapshot is named now, here, so every reader says the same act alike.
    nameless: dict[str, list[str]] = {}
    for mapping in rows:
        if mapping["name"] is None:
            nameless.setdefault(str(mapping["kind"]), []).append(str(mapping["subject_id"]))
    now = await names_now(database, nameless) if nameless else {}
    named: dict[str, list[Thing]] = {}
    for mapping in rows:
        kind, subject_id = str(mapping["kind"]), str(mapping["subject_id"])
        named.setdefault(str(mapping["decision_id"]), []).append(
            Thing(
                kind=kind,
                id=subject_id,
                name=(
                    str(mapping["name"])
                    if mapping["name"] is not None
                    else now.get((kind, subject_id))
                ),
            )
        )
    return named


_OWN_FILTERS = (
    "SELECT s.subject_id AS id, json_extract(d.payload, '$.called') AS name"
    " FROM workbench_decision_subjects s JOIN workbench_decisions d ON d.id = s.decision_id"
    " WHERE s.kind = 'saved_filter' AND json_valid(d.payload)"
    " AND json_extract(d.payload, '$.of') = ? AND s.subject_id IN (?*)"
)


async def own_filters(
    database: Database, viewer: Viewer, ids: Sequence[str]
) -> dict[tuple[str, str], str]:
    """The names of the saved filters among these that were the viewer's."""
    if not ids:
        return {}
    statement, values = in_clause(_OWN_FILTERS, sorted(set(ids)))
    rows = await database.fetch_all(statement, [viewer.id, *values])
    return {("saved_filter", str(row["id"])): str(row["name"]) for row in rows}


#: The id breaks ties only: the backfill minted newer ids for older runs.
_PRESSES_OLDEST_FIRST = """
SELECT d.object_id AS box_id, d.id AS id
  FROM workbench_decision_subjects s
  JOIN workbench_decisions d ON d.id = s.decision_id
 WHERE s.kind = ? AND s.subject_id = ?
   AND d.verb = 'enriched' AND d.object_kind = 'box'
 ORDER BY d.decided_at, d.id
"""


async def first_presses(database: Database, kind: str, subject_id: str) -> dict[str, str]:
    """Each box's first `enriched` event about this thing: box id -> event id."""
    first: dict[str, str] = {}
    for row in await database.fetch_all(_PRESSES_OLDEST_FIRST, (kind, subject_id)):
        if row["box_id"] is not None:
            first.setdefault(str(row["box_id"]), str(row["id"]))
    return first


# The feed's fold: one line per press. A press is a run of acts sharing a key with no gap over
# `FEED_FOLD_GAP`; triggers keep it at the door (`history_presses.PRESS_TRIGGERS`).

#: A minute, so a pass on a slow disk is still one press but a person repeating is not.
FEED_FOLD_GAP: Final = 60

#: The gap for a setting: a sitting, so a dragged slider is one change.
FEED_SITTING_GAP: Final = 600

FEED_FOLD_SHOWN: Final = 100

# `json_valid` first: `json_extract` on text that is not JSON is an error.
_SETTING_KEY = (
    "(CASE WHEN verb = 'edited' AND object_kind IS NULL AND json_valid(payload)"
    " THEN json_extract(payload, '$.key') END)"
)
_DELETED_FROM = (
    "(CASE WHEN verb = 'deleted' AND json_valid(payload) THEN json_extract(payload, '$.from') END)"
)
_BACKFILLED = (
    "(CASE WHEN verb = 'added' AND json_valid(payload)"
    " THEN json_extract(payload, '$.backfilled') END)"
)
_SONG = "(CASE WHEN verb = 'song_named' AND json_valid(payload) THEN json_extract(payload, '$.song') END)"

_SWAP_SESSION = (
    "(CASE WHEN verb = 'added' AND actor_kind = 'sift' AND actor_id = 'swap'"
    " AND json_valid(payload) THEN json_extract(payload, '$.session') END)"
)

_PRESSED_PASSES = "(CASE WHEN verb = 'pressed' AND json_valid(payload) THEN json_extract(payload, '$.passes') END)"

_FOLD_KEY = splice(
    """
CASE
  WHEN verb IN ('ran', 'merged', 'renamed', 'forgot', 'swap_started', 'swap_ended') THEN id
  WHEN verb IN ('sharing_turned_on', 'sharing_turned_off', 'start_with_windows_on',
                'start_with_windows_off', 'firewall_opened', 'storage_moved',
                'update_started', 'library_opened', 'restarted') THEN id
  WHEN {{BACKFILLED}} THEN 'added|backfilled|' || COALESCE(object_kind, '')
  ELSE verb || '|' || queue || '|' || COALESCE(actor_kind, '') || '|'
       || COALESCE(actor_id, '') || '|' || COALESCE(object_kind, '') || '|'
       || CASE
            WHEN verb = 'edited' THEN COALESCE({{SETTING_KEY}}, id)
            WHEN verb = 'decided' THEN title
            WHEN verb = 'song_named' THEN COALESCE(object_id, '') || '|' || COALESCE({{SONG}}, '')
            WHEN {{SWAP_SESSION}} IS NOT NULL THEN {{SWAP_SESSION}}
            WHEN {{PRESSED_PASSES}} IS NOT NULL THEN {{PRESSED_PASSES}}
            WHEN actor_kind IS NULL OR actor_kind = 'user' THEN COALESCE(object_id, '')
            ELSE ''
          END
       || '|' || COALESCE({{DELETED_FROM}}, '')
END""",
    BACKFILLED=_BACKFILLED,
    SETTING_KEY=_SETTING_KEY,
    DELETED_FROM=_DELETED_FROM,
    SONG=_SONG,
    SWAP_SESSION=_SWAP_SESSION,
    PRESSED_PASSES=_PRESSED_PASSES,
)

#: Where each kind of subject with a screen lives, and what it is addressed by.
_STILL_THERE: Final[Mapping[str, str]] = {
    "asset": "SELECT id AS id, id AS address FROM assets WHERE id IN (?*)",
    "person": "SELECT id AS id, id AS address FROM people WHERE id IN (?*)",
    "site": "SELECT id AS id, id AS address FROM sites WHERE id IN (?*)",
    "tag": "SELECT id AS id, id AS address FROM tags WHERE id IN (?*)",
    "collection": "SELECT id AS id, id AS address FROM collections WHERE id IN (?*)",
    "photo_set": "SELECT id AS id, id AS address FROM photo_sets WHERE id IN (?*)",
    "song": "SELECT id AS id, id AS address FROM songs WHERE id IN (?*)",
    "username": "SELECT id AS id, id AS address FROM usernames WHERE id IN (?*)",
    # Probed only for whether it is still there; a user has no page.
    "login": "SELECT id AS id, id AS address FROM users WHERE id IN (?*)",
    "pile": "SELECT id AS id, id AS address FROM face_piles WHERE id IN (?*)",
    "folder": "SELECT id AS id, id AS address FROM folders WHERE id IN (?*)",
    # Safe without the download slice: no such events without its table. A hidden row counts as
    # gone, since the queue cannot show it.
    "download": "SELECT id AS id, id AS address FROM downloads WHERE id IN (?*) AND hidden_at IS NULL",
}


async def subjects_present(
    database: Database, wanted: Mapping[str, Sequence[str]]
) -> dict[tuple[str, str], str]:
    """Which of these things still exist, keyed by kind and id, and what each is addressed by."""
    found: dict[tuple[str, str], str] = {}
    for kind, ids in wanted.items():
        statement = _STILL_THERE.get(kind)
        if statement is None or not ids:
            continue
        asked, values = in_clause(statement, sorted(set(ids)))
        for row in await database.fetch_all(asked, values):
            mapping = dict(row)
            found[(kind, str(mapping["id"]))] = str(mapping["address"])
    return found


def can_be_found(kind: str) -> bool:
    """Whether Sift has a way of asking whether a thing of this kind is still there."""
    return kind in _STILL_THERE


#: What an actor is called, read when the feed is drawn so a renamed user reads by today's name.
_CALLED: Final[Mapping[str, str]] = {
    "user": "SELECT id AS id, username AS name FROM users WHERE id IN (?*)",
    "box": "SELECT id AS id, name AS name FROM stash_boxes WHERE id IN (?*)",
}


async def actor_names(
    database: Database, wanted: Mapping[str, Sequence[str]]
) -> dict[tuple[str, str], str]:
    """What each actor on a page of events is called, keyed by kind and id."""
    named: dict[tuple[str, str], str] = {}
    for kind, ids in wanted.items():
        statement = _CALLED.get(kind)
        if statement is None or not ids:
            continue
        asked, values = in_clause(statement, sorted(set(ids)))
        for row in await database.fetch_all(asked, values):
            mapping = dict(row)
            named[(kind, str(mapping["id"]))] = str(mapping["name"])
    return named
