# SPDX-License-Identifier: AGPL-3.0-or-later
"""The stash-box tables: the configured boxes, their cached answers, links, matches, asks and
kept answers."""

from __future__ import annotations

import json
import time

from sift.kernel.db import Connection, register_schema_initializer
from sift.kernel.ids import new_id
from sift.kernel.migrations import check_allows, widen_a_check
from sift.kernel.sorting import sort_key
from sift.slices.stash_boxes.adapter import from_json, network_name
from sift.slices.stash_boxes.filed_by import backfill as name_the_box_on_every_filing
from sift.slices.stash_boxes.service import Grade, grade_unproven
from sift.slices.stash_boxes.settings import DURATION_DEFAULT_S, DURATION_KEY
from sift.slices.stash_boxes.taken_back import repair as take_back_what_refusals_left

STASH_BOX_COMPONENT = "stash_boxes"
STASH_BOX_VERSION = 21

# `slug` is stored so SQL can group by it; `sites_are` cannot be inferred from an answer.
_CREATE_STASH_BOXES = """
CREATE TABLE IF NOT EXISTS stash_boxes (
  id                  TEXT PRIMARY KEY,
  name                TEXT NOT NULL,
  endpoint            TEXT NOT NULL UNIQUE,
  secret_id           TEXT,
  enabled             INTEGER NOT NULL DEFAULT 1,
  route               TEXT,
  requests_per_minute INTEGER NOT NULL DEFAULT 240,
  sites_are           TEXT NOT NULL DEFAULT 'site' CHECK(sites_are IN ('site','person')),
  slug                TEXT,
  created_at          INTEGER NOT NULL
)
"""

# Cascades with its box: an answer without its service has no provenance.
_CREATE_ANSWERS = """
CREATE TABLE IF NOT EXISTS stash_box_answers (
  box_id     TEXT NOT NULL REFERENCES stash_boxes(id) ON DELETE CASCADE,
  kind       TEXT NOT NULL,
  ask        TEXT NOT NULL,
  payload    TEXT NOT NULL,
  fetched_at INTEGER NOT NULL,
  PRIMARY KEY (box_id, kind, ask)
)
"""

# Three link tables, not one with a `subject` word, so each cascades from its subject and its box.
_CREATE_PERSON_LINKS = """
CREATE TABLE IF NOT EXISTS person_stash_box_links (
  person_id  TEXT NOT NULL REFERENCES people(id)      ON DELETE CASCADE,
  box_id     TEXT NOT NULL REFERENCES stash_boxes(id) ON DELETE CASCADE,
  remote_id  TEXT NOT NULL,
  payload    TEXT NOT NULL,
  fetched_at INTEGER NOT NULL,
  PRIMARY KEY (person_id, box_id)
)
"""

_CREATE_SITE_LINKS = """
CREATE TABLE IF NOT EXISTS site_stash_box_links (
  site_id    TEXT NOT NULL REFERENCES sites(id)       ON DELETE CASCADE,
  box_id     TEXT NOT NULL REFERENCES stash_boxes(id) ON DELETE CASCADE,
  remote_id  TEXT NOT NULL,
  payload    TEXT NOT NULL,
  fetched_at INTEGER NOT NULL,
  PRIMARY KEY (site_id, box_id)
)
"""

_CREATE_TAG_LINKS = """
CREATE TABLE IF NOT EXISTS tag_stash_box_links (
  tag_id     TEXT NOT NULL REFERENCES tags(id)        ON DELETE CASCADE,
  box_id     TEXT NOT NULL REFERENCES stash_boxes(id) ON DELETE CASCADE,
  remote_id  TEXT NOT NULL,
  payload    TEXT NOT NULL,
  fetched_at INTEGER NOT NULL,
  PRIMARY KEY (tag_id, box_id)
)
"""

# One row per file per box; `payload` keeps the whole record for the confirm screen and undo.
_CREATE_MATCHES = """
CREATE TABLE IF NOT EXISTS asset_stash_box_matches (
  asset_id   TEXT NOT NULL REFERENCES assets(id)        ON DELETE CASCADE,
  box_id     TEXT NOT NULL REFERENCES stash_boxes(id)   ON DELETE CASCADE,
  remote_id  TEXT NOT NULL,
  payload    TEXT NOT NULL,
  grade      TEXT NOT NULL CHECK(grade IN ('certain','likely','unsure')),
  state      TEXT NOT NULL DEFAULT 'waiting'
             CHECK(state IN ('waiting','applied','refused')),
  found_at   INTEGER NOT NULL,
  decided_at INTEGER,
  PRIMARY KEY (asset_id, box_id)
)
"""

# Every ask, so a file no service knows is not asked again on every run.
_CREATE_SCANS = """
CREATE TABLE IF NOT EXISTS stash_box_scans (
  id         TEXT PRIMARY KEY,
  asset_id   TEXT NOT NULL REFERENCES assets(id)      ON DELETE CASCADE,
  box_id     TEXT NOT NULL REFERENCES stash_boxes(id) ON DELETE CASCADE,
  scanned_at INTEGER NOT NULL,
  found      INTEGER NOT NULL DEFAULT 0
)
"""

# A note, swept by its reader: the subject may be in any of three tables.
_CREATE_UNDECIDED = """
CREATE TABLE IF NOT EXISTS stash_box_undecided (
  subject    TEXT NOT NULL,
  local_id   TEXT NOT NULL,
  candidates INTEGER NOT NULL,
  seen_at    INTEGER NOT NULL,
  PRIMARY KEY (subject, local_id)
)
"""

# Once-only passes that have run, since their links may since have been taken off.
_CREATE_CATCH_UPS = """
CREATE TABLE IF NOT EXISTS stash_box_catch_ups (
  name   TEXT PRIMARY KEY,
  ran_at INTEGER NOT NULL
)
"""

# Keeping your own value writes nothing else, so it is stored with both values it answered.
_CREATE_KEPT = """
CREATE TABLE IF NOT EXISTS stash_box_kept (
  subject    TEXT NOT NULL,
  local_id   TEXT NOT NULL,
  box_id     TEXT NOT NULL REFERENCES stash_boxes(id) ON DELETE CASCADE,
  key        TEXT NOT NULL,
  mine       TEXT NOT NULL,
  theirs     TEXT NOT NULL,
  decided_at INTEGER NOT NULL,
  PRIMARY KEY (subject, local_id, box_id, key)
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_stash_matches_state ON asset_stash_box_matches(state, found_at)",
    "CREATE INDEX IF NOT EXISTS ix_stash_matches_box ON asset_stash_box_matches(box_id)",
    "CREATE INDEX IF NOT EXISTS ix_stash_scans_asked ON stash_box_scans(box_id, asset_id)",
    "CREATE INDEX IF NOT EXISTS ix_stash_scans_box ON stash_box_scans(box_id)",
    "CREATE INDEX IF NOT EXISTS ix_stash_scans_asset ON stash_box_scans(asset_id, box_id, scanned_at)",
    "CREATE INDEX IF NOT EXISTS ix_stash_answers_age ON stash_box_answers(fetched_at)",
    "CREATE INDEX IF NOT EXISTS ix_person_links_box ON person_stash_box_links(box_id)",
    "CREATE INDEX IF NOT EXISTS ix_site_stash_box_links_box ON site_stash_box_links(box_id)",
    "CREATE INDEX IF NOT EXISTS ix_tag_links_box ON tag_stash_box_links(box_id)",
)

#: Version 16: added in place, since a rebuild would cascade into six child tables.
_SITES_ARE_UNCHECKED = "sites_are TEXT NOT NULL DEFAULT 'site'"
_SITES_ARE_CHECKED = "sites_are TEXT NOT NULL DEFAULT 'site' CHECK(sites_are IN ('site','person'))"
_STORED_BOXES = "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'stash_boxes'"
_OUTSIDE_THE_CHECK = "SELECT 1 FROM stash_boxes WHERE sites_are NOT IN ('site','person') LIMIT 1"


async def _check_what_boxes_are(connection: Connection) -> None:
    """Give `sites_are` its CHECK where the stored definition lacks one."""
    if await check_allows(connection, "stash_boxes", "person"):
        return
    rows = list(await connection.execute_fetchall(_STORED_BOXES))
    stored = str(rows[0][0]) if rows and rows[0][0] is not None else ""
    if stored.count(_SITES_ARE_UNCHECKED) != 1:
        return
    if list(await connection.execute_fetchall(_OUTSIDE_THE_CHECK)):
        return
    await widen_a_check(connection, "stash_boxes", was=_SITES_ARE_UNCHECKED, now=_SITES_ARE_CHECKED)


#: Version 17: answers graded certain only because a hash was sent, regraded as perceptual.
_KEPT_CERTAIN = """
SELECT m.asset_id, m.box_id, m.payload, a.duration_ms
  FROM asset_stash_box_matches m JOIN assets a ON a.id = m.asset_id
 WHERE m.grade = 'certain' AND m.state <> 'refused'
"""
_REGRADE = "UPDATE asset_stash_box_matches SET grade = ? WHERE asset_id = ? AND box_id = ?"
_SETTINGS_HERE = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'app_settings'"
_TOLERANCE = "SELECT value FROM app_settings WHERE key = ?"


async def _tolerance_ms(connection: Connection) -> int:
    seconds: object = DURATION_DEFAULT_S
    if list(await connection.execute_fetchall(_SETTINGS_HERE)):
        rows = list(await connection.execute_fetchall(_TOLERANCE, (DURATION_KEY,)))
        if rows and rows[0][0] is not None:
            try:
                seconds = json.loads(str(rows[0][0]))
            except ValueError:
                seconds = 0
    try:
        return max(0, int(str(seconds))) * 1000
    except ValueError:
        return 0


async def _grade_what_was_unproven(connection: Connection) -> None:
    """Regrade every kept certain answer by what its record can prove."""
    tolerance_ms = await _tolerance_ms(connection)
    for row in list(await connection.execute_fetchall(_KEPT_CERTAIN)):
        records = from_json(str(row["payload"]))
        if not records:
            continue
        length = None if row["duration_ms"] is None else int(row["duration_ms"])
        grade = grade_unproven(records[0], length_ms=length, tolerance_ms=tolerance_ms)
        if grade is not Grade.CERTAIN:
            await connection.execute(_REGRADE, (grade.value, row["asset_id"], row["box_id"]))


#: Version 18: a parent Site made from a box's answer, attributed to that box.
_ATTRIBUTE_PARENTS = """
UPDATE sites
   SET created_by_box_id = (
       SELECT l.box_id FROM sites k
         JOIN site_stash_box_links l ON l.site_id = k.id
         JOIN stash_boxes b ON b.id = l.box_id
        WHERE k.parent_id = sites.id
          AND CASE WHEN json_valid(l.payload)
                   THEN json_extract(l.payload, '$[0].fields.parent') END = sites.name COLLATE NOCASE
        ORDER BY l.fetched_at, l.box_id LIMIT 1),
       created_by_kind = 'box', created_by_via = 'stash', created_by_user_id = NULL
 WHERE created_by_box_id IS NULL
   AND (created_by_kind = 'box'
        OR (created_by_kind = 'user' AND (created_by_user_id IS NULL OR created_at IS NULL)))
   AND EXISTS (
       SELECT l.box_id FROM sites k
         JOIN site_stash_box_links l ON l.site_id = k.id
         JOIN stash_boxes b ON b.id = l.box_id
        WHERE k.parent_id = sites.id
          AND CASE WHEN json_valid(l.payload)
                   THEN json_extract(l.payload, '$[0].fields.parent') END = sites.name COLLATE NOCASE
        ORDER BY l.fetched_at, l.box_id LIMIT 1)
"""


#: A network named with the box's "(Network)" suffix takes its own name; the suffix stays an alias.
_NETWORKS_SPELT_BY_A_BOX = """
SELECT s.id AS id, s.name AS name FROM sites s
 WHERE s.name LIKE '%(network)%' AND EXISTS (SELECT 1 FROM sites k WHERE k.parent_id = s.id)
 ORDER BY s.id
"""
_NAME_HELD = "SELECT 1 FROM sites WHERE name = ? AND id <> ?"
_RENAME_NETWORK = "UPDATE sites SET name = ?, name_sort = ? WHERE id = ?"
_KEEP_THE_BOXS_SPELLING = (
    "INSERT OR IGNORE INTO site_aliases (id, site_id, alias, alias_sort, added_at)"
    " VALUES (?, ?, ?, ?, ?)"
)


async def _call_networks_by_their_names(connection: Connection) -> None:
    """Rename each parent Site the box's bracket names, keeping that spelling as an alias."""
    now = int(time.time())
    for row in list(await connection.execute_fetchall(_NETWORKS_SPELT_BY_A_BOX)):
        spelt = str(row["name"])
        bare = network_name(spelt)
        if bare == spelt:
            continue
        if list(await connection.execute_fetchall(_NAME_HELD, (bare, row["id"]))):
            continue
        await connection.execute(_RENAME_NETWORK, (bare, sort_key(bare), row["id"]))
        await connection.execute(
            _KEEP_THE_BOXS_SPELLING, (new_id(), row["id"], spelt, sort_key(spelt), now)
        )


async def initialize_stash_boxes(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        for statement in (
            _CREATE_STASH_BOXES,
            _CREATE_ANSWERS,
            _CREATE_PERSON_LINKS,
            _CREATE_SITE_LINKS,
            _CREATE_TAG_LINKS,
            _CREATE_MATCHES,
            _CREATE_SCANS,
            _CREATE_UNDECIDED,
            _CREATE_CATCH_UPS,
            _CREATE_KEPT,
            *_INDEXES,
        ):
            await connection.execute(statement)
    if 0 < on_disk < 16:
        await _check_what_boxes_are(connection)
    if 0 < on_disk < 17:
        await _grade_what_was_unproven(connection)
    if 0 < on_disk < 18:
        await connection.execute(_ATTRIBUTE_PARENTS)
        await _call_networks_by_their_names(connection)
    if 0 < on_disk < 19:
        # Take back what a refused answer filed, with one History line and its Undo.
        await take_back_what_refusals_left(connection)
    if 0 < on_disk < 20:
        await take_back_what_refusals_left(connection, asked_again=True)
    if 0 < on_disk < 21:
        await name_the_box_on_every_filing(connection)


register_schema_initializer(
    STASH_BOX_COMPONENT,
    STASH_BOX_VERSION,
    initialize_stash_boxes,
    depends_on=["identity", "catalog", "content"],
    baseline=15,
)
