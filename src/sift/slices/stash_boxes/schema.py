# SPDX-License-Identifier: AGPL-3.0-or-later
"""What Sift remembers about the stash-boxes it can ask, and what they have already answered.

Ten tables. `stash_boxes` is the list somebody configured: a name, an address, and the id of the
sealed key in `secrets`. The key itself is never here: it goes through the kernel's secret store,
sealed under the master key, exactly as a site login does.

`stash_box_answers` is the per-instance cache. It exists so that asking the same question twice in
an afternoon costs one request rather than two, and it is per instance on purpose: Sift ships no
mirror of anybody's stash-box and redistributing one is not on the table.

Three of them are the LINKS (what a box calls a person, a Site or a tag Sift already has),
and two more are the bulk pass: what it found, and which files it has already asked about. The
second of those is what stops the pass asking three public services about the same unrecognised
file every time it runs.

The last three are notes rather than records. `stash_box_undecided` is what the unattended pass could
not choose between; `stash_box_catch_ups` is which once-only passes have run, so that a pass which
must happen once per box cannot happen twice; `stash_box_kept` is the one answer
to a disagreement that leaves no trace anywhere else (keeping your own value) and the pair of
values it was an answer to. See `_CREATE_KEPT` for why that one is stored when the conflict itself
deliberately is not.
"""

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

# `endpoint` is unique because two rows for one address are two throttles for one service, which is
# precisely the thing the throttle exists to prevent.
#
# `requests_per_minute` is stored per box rather than hard-coded. The default, 240, is what Stash's
# own client paces itself at against these same services; storing it means an admin whose account
# is told to slow down can slow down without waiting for a release.
#
# `sites_are` is what this box's studios are here: a Site, or a Person. It cannot be inferred and it
# decides where an answer lands: on StashDB and FansDB the entries a box files as studios are
# production companies, which are Sites here; on PMVStash those same entries are the CREATORS.
# 'site' is the default because it is what every box but one is. The set a CHECK accepts cannot be
# changed without rebuilding the table, and this one is a parent of six that cascade.
#
# `slug` is the word this box is known by here: `stashdb`, `fansdb`, `pmvstash`, NULL for a box
# Sift has never heard of. Derived from the endpoint by `known_boxes.slug_for` and STORED, because
# SQL has to group and filter by it and the alternative is a second copy of the host table written
# in SQL, the substring guess `known_boxes` refuses.
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

# One answer, keyed by what was asked. `fetched_at` is what makes it a cache rather than a copy:
# every row has a moment on it and is re-asked once it is old enough.
#
# ON DELETE CASCADE, so removing a box takes what it said with it. A cached answer outliving the
# service it came from is a fact with no provenance, which is worse than no fact.
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

# --- The links -------------------------------------------------------------------------------
#
# What a stash-box calls something Sift already has. THREE tables of one shape, one per kind of
# subject, and that is deliberate rather than an oversight of the obvious economy.
#
# One table with a `subject` word beside the id, as `acl_grants` holds every kind of grantable
# thing, would not cascade: a foreign key cannot point at "whichever kind of thing this is".
# `acl_grants` is swept by the kernel, which every slice can reach; this table belongs to a slice
# that the three slices deleting these subjects may not import, so nothing could sweep it.
#
# Cascading twice, from both ends. The subject going takes its links; the BOX going takes them too,
# because a link is a statement about one service and it means nothing once that service is not
# configured any more.
#
# `payload` is the whole answer, kept. Not a duplicate of the cache beside it: the cache is keyed by
# the question and expires, and this is keyed by the subject and never does. It is what lets "show
# every stash-box field" draw a field Sift has no column for, and what lets a later version promote
# a field to a column without asking the services again.
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

# --- What the bulk pass found ------------------------------------------------------------------
#
# One row per file per box, and it is the whole of the pass's memory: what was found, how sure Sift
# is, and whether somebody has answered it. Cascading from both ends, like the links above: a file
# that has gone has no matches, and a box that is no longer configured has said nothing.
#
# `payload` is the whole record, kept. The confirm screen draws every field it would write and the
# undo needs to know what was written, and neither can ask a public service again a week later for
# a file somebody has since deleted.
#
# `grade` is Sift's own reading and never the stash-box's. An exact-file hash is an identity; a
# perceptual match whose length agrees is strong; one whose length was never checked is a candidate
# somebody still has to agree with.
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

# Every ASK, whatever the answer was: one row per ask of one box about one file, so a History can
# name each one and not only the latest.
#
# Its own table rather than being inferred from the matches: a file no service has heard of
# produces no match row, so a sweep that read only the matches would ask about it again on every
# run, through a deliberate throttle. This is what makes the second sweep cheap.
#
# Per box, because a box configured later has genuinely not been asked. `found` is kept so the
# screen can say how much of the library the stash-boxes recognise at all, which is the one number
# that says whether the feature is worth leaving on.
#
# The cost: this table grows with the number of asks rather than files, about fifty bytes a row, so
# a hundred thousand files asked of three boxes is about 15 MB per full re-sweep. It has no horizon
# yet; one belongs here once the sweep's real cadence is known.
_CREATE_SCANS = """
CREATE TABLE IF NOT EXISTS stash_box_scans (
  id         TEXT PRIMARY KEY,
  asset_id   TEXT NOT NULL REFERENCES assets(id)      ON DELETE CASCADE,
  box_id     TEXT NOT NULL REFERENCES stash_boxes(id) ON DELETE CASCADE,
  scanned_at INTEGER NOT NULL,
  found      INTEGER NOT NULL DEFAULT 0
)
"""

# What the unattended enrichment could not decide: a subject one box holds more than one certain
# entry for. Kept so a person can be shown the list and settle each from the chooser on the
# subject's own page; a row goes the moment the subject is linked. No foreign key on the subject
# because it is one of three tables: the row is a note, and a note about a row that has gone is
# swept by the reader rather than by the engine.
_CREATE_UNDECIDED = """
CREATE TABLE IF NOT EXISTS stash_box_undecided (
  subject    TEXT NOT NULL,
  local_id   TEXT NOT NULL,
  candidates INTEGER NOT NULL,
  seen_at    INTEGER NOT NULL,
  PRIMARY KEY (subject, local_id)
)
"""

# Which once-only passes have run, by name: the linking of the people a box invented before they
# were linked to it, one row per box (`jobs.invented_pass`). A pass that must run once cannot be
# inferred from the data, since the links it makes are exactly the links somebody may since have
# taken off, so it is remembered. `ran_at` is kept for the operator reading the database; nothing
# branches on it. Its own table rather than a setting, because nobody chooses it and nothing draws
# it.
_CREATE_CATCH_UPS = """
CREATE TABLE IF NOT EXISTS stash_box_catch_ups (
  name   TEXT PRIMARY KEY,
  ran_at INTEGER NOT NULL
)
"""

# The one answer to a disagreement that writes nothing anywhere else: keeping your own value.
#
# ## Why this table exists alongside "worked out rather than stored"
#
# A conflict is the state of two values and is never a stored row (see `reconcile`). This stores
# the answer somebody gave to one, which exists nowhere else: taking the box's value writes the
# field, so the disagreement ends on its own, while keeping your own writes nothing, and without
# this row the next read would work the same conflict out again.
#
# ## The two values are stored with the answer
#
# A decision is about a pair of values, not about a field. If either side moves (somebody edits
# the record, the box is asked again and says something new), the question is a different question
# and has to come back. So the row carries both sides as they were when it was answered, and the
# read skips a conflict only where both still match. Keyed on the field rather than on the values
# so a second answer replaces the first instead of piling up.
#
# The box cascades: a row here is an answer about what one box said, and a box that has been
# removed is not saying anything any more.
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
    # The Tagger panel's only question: what is still waiting, and how much of it.
    "CREATE INDEX IF NOT EXISTS ix_stash_matches_state ON asset_stash_box_matches(state, found_at)",
    # "Which of my files has THIS box matched", and what a re-key has to throw away.
    "CREATE INDEX IF NOT EXISTS ix_stash_matches_box ON asset_stash_box_matches(box_id)",
    # What the sweep asks of the asks: has this box been asked about this file. Any row at all,
    # not how many. `ix_stash_scans_box` is the same question the other way round.
    "CREATE INDEX IF NOT EXISTS ix_stash_scans_asked ON stash_box_scans(box_id, asset_id)",
    "CREATE INDEX IF NOT EXISTS ix_stash_scans_box ON stash_box_scans(box_id)",
    # One file's asks, newest last: what a History draws, and a seek rather than a walk.
    "CREATE INDEX IF NOT EXISTS ix_stash_scans_asset ON stash_box_scans(asset_id, box_id, scanned_at)",
    # Sweeping stale rows asks by age across every box at once.
    "CREATE INDEX IF NOT EXISTS ix_stash_answers_age ON stash_box_answers(fetched_at)",
    # "Which of my people are linked to THIS box": what the box's own row shows, and what a re-key
    # has to invalidate. The other direction is the primary key.
    "CREATE INDEX IF NOT EXISTS ix_person_links_box ON person_stash_box_links(box_id)",
    "CREATE INDEX IF NOT EXISTS ix_site_stash_box_links_box ON site_stash_box_links(box_id)",
    "CREATE INDEX IF NOT EXISTS ix_tag_links_box ON tag_stash_box_links(box_id)",
)

#: Version 16: the CHECK on `sites_are`, for a library whose column was added before it carried one.
#: Written into the stored definition where the constraint lives (`widen_a_check`), because this
#: table is a parent of six that cascade and a rebuild would take their rows. Only where every
#: stored value already passes it, which it does wherever `known_boxes.sites_are_for` wrote them.
_SITES_ARE_UNCHECKED = "sites_are TEXT NOT NULL DEFAULT 'site'"
_SITES_ARE_CHECKED = "sites_are TEXT NOT NULL DEFAULT 'site' CHECK(sites_are IN ('site','person'))"
_STORED_BOXES = "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'stash_boxes'"
_OUTSIDE_THE_CHECK = "SELECT 1 FROM stash_boxes WHERE sites_are NOT IN ('site','person') LIMIT 1"


async def _check_what_boxes_are(connection: Connection) -> None:
    """Give `sites_are` its CHECK where the stored definition lacks one. See v16 above."""
    if await check_allows(connection, "stash_boxes", "person"):
        return
    rows = list(await connection.execute_fetchall(_STORED_BOXES))
    stored = str(rows[0][0]) if rows and rows[0][0] is not None else ""
    if stored.count(_SITES_ARE_UNCHECKED) != 1:
        return
    if list(await connection.execute_fetchall(_OUTSIDE_THE_CHECK)):
        return
    await widen_a_check(connection, "stash_boxes", was=_SITES_ARE_UNCHECKED, now=_SITES_ARE_CHECKED)


#: Version 17: every kept answer graded certain, regraded as the perceptual match it may have been.
#:
#: Such an answer was graded certain whenever an exact hash had been SENT, which it always was, and
#: its record keeps nothing that says which hash the box matched. So each is read as a perceptual
#: match (`service.grade_unproven`): certain still only on a long file whose length agrees closely.
#: The refused are left alone (nobody is asked about them again). Taking the demoted applied ones
#: back off their files is a one-time pass that has run and is retired; the History lines it wrote
#: still read (`sentences.TOOK_BACK`).
_KEPT_CERTAIN = """
SELECT m.asset_id, m.box_id, m.payload, a.duration_ms
  FROM asset_stash_box_matches m JOIN assets a ON a.id = m.asset_id
 WHERE m.grade = 'certain' AND m.state <> 'refused'
"""
_REGRADE = "UPDATE asset_stash_box_matches SET grade = ? WHERE asset_id = ? AND box_id = ?"
_SETTINGS_HERE = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'app_settings'"
_TOLERANCE = "SELECT value FROM app_settings WHERE key = ?"


async def _tolerance_ms(connection: Connection) -> int:
    """The length window somebody set, in milliseconds, or the default where none is stored."""
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
    """Regrade every kept certain answer by what its record can prove. See v17 above."""
    tolerance_ms = await _tolerance_ms(connection)
    for row in list(await connection.execute_fetchall(_KEPT_CERTAIN)):
        records = from_json(str(row["payload"]))
        if not records:
            continue
        length = None if row["duration_ms"] is None else int(row["duration_ms"])
        grade = grade_unproven(records[0], length_ms=length, tolerance_ms=tolerance_ms)
        if grade is not Grade.CERTAIN:
            await connection.execute(_REGRADE, (grade.value, row["asset_id"], row["box_id"]))


#: Version 18: a parent Site a box's answer named, attributed to that box.
#:
#: Filling a studio's record from a box made its parent network by name, as though a person had
#: typed it: kind 'user' with no user, no box and no link, so its header said "Created by
#: somebody" and it wore a letter. The evidence it was the box's is still here: a Site linked to a
#: box whose kept record names this Site as its parent, and points at it. Such a row is recorded as
#: that box's, the way `catalog.mark_created_by_box` records it, and the linking pass then asks
#: that box for its studio (`jobs.link_what_boxes_invented`). The same for a row that says 'box'
#: and no longer says which, and for a 'user' row with no creation time: its maker was filled in
#: afterwards, not recorded when it was made. The first link to name it wins, so the answer never
#: depends on order.
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


#: And a network called by the box's "(Network)" spelling is called by its own name, the way a
#: network the box names from now on is made (`adapter.network_name`). Only a Site that is a parent,
#: and only where no other Site already holds the bare name: a network and its flagship studio can
#: share one, and two Sites of one name cannot exist. The box's spelling stays as an alias, which
#: the linking pass searches by (`service._INVENTED_SITES_UNLINKED`).
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
    """Rename each parent Site the box's bracket names, keeping that spelling. See v18 above."""
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
        # What a refused answer filed on its file, taken back once with one History line and its
        # Undo, and the usernames, people, Sites and tags that leaves holding nothing
        # (`taken_back.repair`).
        await take_back_what_refusals_left(connection)
    if 0 < on_disk < 20:
        # The same, for an applied answer a re-ask reopened and left written.
        await take_back_what_refusals_left(connection, asked_again=True)
    if 0 < on_disk < 21:
        # Which box made each older box filing, with a History line and a log line (`filed_by`).
        await name_the_box_on_every_filing(connection)


register_schema_initializer(
    STASH_BOX_COMPONENT,
    STASH_BOX_VERSION,
    initialize_stash_boxes,
    # The link tables point at people, sites and tags, and the match tables point at assets, so
    # both the catalog and the content model have to exist first.
    depends_on=["identity", "catalog", "content"],
    baseline=15,
)
