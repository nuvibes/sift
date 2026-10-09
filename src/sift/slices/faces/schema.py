# SPDX-License-Identifier: AGPL-3.0-or-later
"""The face tables: what was found, what it was matched against, and how far a scan got.

A schema component of its own, since most installs never turn faces on. The unit is a face, never
a file; `face_asset_people` records only which names on a file this feature put there. Crops live
under the data directory, as a reference crop cannot be rebuilt.
"""

from __future__ import annotations

from sift.kernel.access.visibility import WAITING_FACES_KIND, Counted, register_counted
from sift.kernel.content.backlog import marks
from sift.kernel.db import Connection, register_schema_initializer
from sift.kernel.migrations import check_allows, column_exists, widen_a_check

COMPONENT = "faces"
VERSION = 44

# Where a scan got to, per asset; never scanned is no row. The digest, shape, density and versions
# say whether a stored pass is at least as thorough as today's tuning (NULL never is). `reached_ms`
# and `cut_short` let a pass carry on; the `refused_*` counts say why a file found nobody (NULL is
# not known).
_CREATE_SCANS = """
CREATE TABLE IF NOT EXISTS face_scans (
  asset_id         TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
  status           TEXT NOT NULL
                   CHECK(status IN ('no_faces','none_identified','some_identified','all_identified')),
  depth            TEXT NOT NULL CHECK(depth IN ('fast','deep')),
  coverage         REAL NOT NULL,
  frames_sampled   INTEGER NOT NULL,
  track_count      INTEGER NOT NULL,
  identified_count INTEGER NOT NULL,
  detector         TEXT NOT NULL,
  recognizer       TEXT NOT NULL,
  settings_digest  TEXT NOT NULL,
  settings_density REAL,
  quality_version  INTEGER,
  sampling_version INTEGER,
  scanned_at       INTEGER NOT NULL,
  reached_ms       INTEGER,
  settings_shape   TEXT,
  refused_small    INTEGER,
  refused_closer   INTEGER,
  cut_short        INTEGER,
  refused_largest  INTEGER,
  refused_blurred  INTEGER,
  refused_turned   INTEGER,
  refused_edge     INTEGER
)
"""


# What a screen's mark and a rescan narrow by: stored values, not a join through the crops (v24).
_INDEX_REFERENCE_TRACK = (
    "CREATE INDEX IF NOT EXISTS ix_face_references_track ON face_references(track_id)"
)
_INDEX_REFERENCE_ASSET = (
    "CREATE INDEX IF NOT EXISTS ix_face_references_asset ON face_references(person_id, asset_id)"
)

# The tuning one sweep runs under, captured when it starts, so a mid-sweep change does not split
# it; the machine limits stay live. One JSON snapshot, handed back whole.
_CREATE_RUNS = """
CREATE TABLE IF NOT EXISTS face_runs (
  id         TEXT PRIMARY KEY,
  tuning     TEXT NOT NULL,
  created_at INTEGER NOT NULL
)
"""


# A pile of unidentified faces that resemble each other. `ignored` keeps it listed and reversible;
# `by_hand` marks one somebody built, which regrouping leaves alone.
_CREATE_PILES = """
CREATE TABLE IF NOT EXISTS face_piles (
  id         TEXT PRIMARY KEY,
  status     TEXT NOT NULL CHECK(status IN ('open','ignored')),
  centroid   BLOB NOT NULL,
  size       INTEGER NOT NULL,
  by_hand    INTEGER NOT NULL DEFAULT 0,
  recognizer TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
)
"""


# One appearance of one face; `attribution` says how its person got there.
_CREATE_TRACKS = """
CREATE TABLE IF NOT EXISTS face_tracks (
  id           TEXT PRIMARY KEY,
  asset_id     TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  started_ms   INTEGER NOT NULL,
  ended_ms     INTEGER NOT NULL,
  seen_in      INTEGER NOT NULL,
  quality      REAL NOT NULL,
  person_id    TEXT REFERENCES people(id) ON DELETE SET NULL,
  confidence   REAL,
  attribution  TEXT CHECK(attribution IN ('matched','suggested','confirmed')),
  pile_id      TEXT REFERENCES face_piles(id) ON DELETE SET NULL,
  attributed_at INTEGER,
  created_at   INTEGER NOT NULL,
  asked_by     TEXT CHECK(asked_by IN ('match','group','undone','box'))
)
"""

# v32: which face each old appearance was found again as after a rescan, pointed at the newest, so
# an Undo naming an old id reaches the face found again.
_CREATE_SUCCESSORS = """
CREATE TABLE IF NOT EXISTS face_successors (
  track_id     TEXT PRIMARY KEY,
  successor_id TEXT NOT NULL,
  asset_id     TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  created_at   INTEGER NOT NULL
)
"""

_INDEX_SUCCESSORS_SUCCESSOR = (
    "CREATE INDEX IF NOT EXISTS ix_face_successors_successor ON face_successors(successor_id)"
)
# v33: somebody every one of whose stash-box pictures this model refused as a starter (or who has
# none, `pictures` 0), so the starters count does not offer them for ever.
_CREATE_STARTER_REFUSALS = """
CREATE TABLE IF NOT EXISTS face_starter_refusals (
  person_id   TEXT PRIMARY KEY REFERENCES people(id) ON DELETE CASCADE,
  recognizer  TEXT NOT NULL,
  pictures    INTEGER NOT NULL,
  refused_at  INTEGER NOT NULL
)
"""

_INDEX_SUCCESSORS_ASSET = (
    "CREATE INDEX IF NOT EXISTS ix_face_successors_asset ON face_successors(asset_id)"
)


# The numbers and the picture behind one face at one moment, a few per track, kept so a new person
# or model is arithmetic, not a re-decode; the three measurements say where the bar went wrong.
_CREATE_DETECTIONS = """
CREATE TABLE IF NOT EXISTS face_detections (
  id           TEXT PRIMARY KEY,
  track_id     TEXT NOT NULL REFERENCES face_tracks(id) ON DELETE CASCADE,
  timestamp_ms INTEGER NOT NULL,
  box_x        INTEGER NOT NULL,
  box_y        INTEGER NOT NULL,
  box_w        INTEGER NOT NULL,
  box_h        INTEGER NOT NULL,
  score        REAL NOT NULL,
  quality      REAL NOT NULL,
  pixels       INTEGER,
  sharpness    REAL,
  frontality   REAL,
  containment  REAL,
  strength     REAL,
  agreement    REAL,
  crop_path    TEXT NOT NULL,
  crop_digest  TEXT NOT NULL,
  embedding    BLOB NOT NULL,
  created_at   INTEGER NOT NULL
)
"""


# Faces somebody removed, remembered by description and file so a rescan does not bring them back;
# the measurements are evidence about where the quality bar sits.
_CREATE_REMOVED = """
CREATE TABLE IF NOT EXISTS face_removals (
  id           TEXT PRIMARY KEY,
  asset_id     TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  embedding    BLOB NOT NULL,
  recognizer   TEXT NOT NULL,
  quality      REAL NOT NULL,
  pixels       INTEGER,
  sharpness    REAL,
  frontality   REAL,
  created_at   INTEGER NOT NULL
)
"""

_INDEX_REMOVED_ASSET = (
    "CREATE INDEX IF NOT EXISTS ix_face_removals_asset ON face_removals(asset_id)"
)

# v11: faces set aside, remembered by description so a rescan keeps them aside, with their pile.
_CREATE_IGNORED = """
CREATE TABLE IF NOT EXISTS face_ignored (
  id           TEXT PRIMARY KEY,
  asset_id     TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  pile_id      TEXT NOT NULL,
  embedding    BLOB NOT NULL,
  centroid     BLOB NOT NULL,
  recognizer   TEXT,
  created_at   INTEGER NOT NULL
)
"""

_INDEX_IGNORED_ASSET = "CREATE INDEX IF NOT EXISTS ix_face_ignored_asset ON face_ignored(asset_id)"

# v11: the faces somebody confirmed, one row per appearance by its best description.
_CREATE_CONFIRMATIONS = """
CREATE TABLE IF NOT EXISTS face_confirmations (
  id           TEXT PRIMARY KEY,
  asset_id     TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  person_id    TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  embedding    BLOB NOT NULL,
  recognizer   TEXT,
  created_at   INTEGER NOT NULL
)
"""

_INDEX_CONFIRMED_ASSET = (
    "CREATE INDEX IF NOT EXISTS ix_face_confirmations_asset ON face_confirmations(asset_id)"
)

# v22: a person's confirmations by day; covering, so the read never touches the table.
_INDEX_CONFIRMED_PERSON = (
    "CREATE INDEX IF NOT EXISTS ix_face_confirmations_person"
    " ON face_confirmations(person_id, created_at, asset_id)"
)


# v13: faces grouped by hand, remembered by description and pile so a rescan keeps the grouping.
_CREATE_GROUPING = """
CREATE TABLE IF NOT EXISTS face_grouping (
  id           TEXT PRIMARY KEY,
  asset_id     TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  pile_id      TEXT NOT NULL,
  embedding    BLOB NOT NULL,
  centroid     BLOB NOT NULL,
  recognizer   TEXT,
  created_at   INTEGER NOT NULL
)
"""

_INDEX_GROUPING_ASSET = (
    "CREATE INDEX IF NOT EXISTS ix_face_grouping_asset ON face_grouping(asset_id)"
)


# An installed pack, by the library that made it, or by its name where the file names none.
_CREATE_PACKS = """
CREATE TABLE IF NOT EXISTS face_packs (
  id           TEXT PRIMARY KEY,
  name         TEXT NOT NULL UNIQUE COLLATE NOCASE,
  version      TEXT NOT NULL,
  recognizer   TEXT NOT NULL,
  dimension    INTEGER NOT NULL,
  digest       TEXT NOT NULL,
  installed_at INTEGER NOT NULL,
  library      TEXT
)
"""

_INDEX_PACKS_LIBRARY = (
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_face_packs_library ON face_packs(library)"
    " WHERE library IS NOT NULL"
)

# This library's own random id for the files it makes.
_CREATE_OWN_LIBRARY = """
CREATE TABLE IF NOT EXISTS face_own_library (
  one INTEGER PRIMARY KEY CHECK (one = 1),
  id  TEXT NOT NULL
)
"""


_CREATE_REFERENCES = """
CREATE TABLE IF NOT EXISTS face_references (
  id          TEXT PRIMARY KEY,
  person_id   TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  crop_path   TEXT,
  crop_digest TEXT NOT NULL,
  embedding   BLOB NOT NULL,
  quality     REAL NOT NULL,
  origin      TEXT NOT NULL CHECK(origin IN ('added','pack','confirmed','seed','recognized')),
  pack_id     TEXT REFERENCES face_packs(id) ON DELETE CASCADE,
  recognizer  TEXT NOT NULL,
  pixels      INTEGER,
  track_id    TEXT,
  asset_id    TEXT,
  created_at  INTEGER NOT NULL,
  source      TEXT,
  retired_at  INTEGER,
  UNIQUE(person_id, crop_digest)
)
"""

# "This face is not that person": read by every match, never expired; `face_rejected` carries it
# across a rescan.
_CREATE_REJECTIONS = """
CREATE TABLE IF NOT EXISTS face_rejections (
  track_id   TEXT NOT NULL REFERENCES face_tracks(id) ON DELETE CASCADE,
  person_id  TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL,
  PRIMARY KEY(track_id, person_id)
)
"""

# v22: refusals by person, covering for the same reason as the confirmations' index.
_INDEX_REJECTED_PERSON = (
    "CREATE INDEX IF NOT EXISTS ix_face_rejections_person"
    " ON face_rejections(person_id, created_at, track_id)"
)

# v27: a refusal remembered by description and file, so a rescan keeps the "no"; `created_at` is
# carried back so it is not shown as said again.
_CREATE_REJECTED = """
CREATE TABLE IF NOT EXISTS face_rejected (
  id           TEXT PRIMARY KEY,
  asset_id     TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  person_id    TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  embedding    BLOB NOT NULL,
  recognizer   TEXT,
  created_at   INTEGER NOT NULL
)
"""

_INDEX_REJECTED_ASSET = (
    "CREATE INDEX IF NOT EXISTS ix_face_rejected_asset ON face_rejected(asset_id)"
)


# Which names on a file this feature put there, so withdrawing removes only its own claim; keyed by
# the pair, which stands until the last face naming that person goes.
_CREATE_CLAIMS = """
CREATE TABLE IF NOT EXISTS face_asset_people (
  asset_id   TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  person_id  TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL,
  PRIMARY KEY(asset_id, person_id)
)
"""

# Which model files are on this machine, with the digest that proves they have not changed.
_CREATE_WEIGHTS = """
CREATE TABLE IF NOT EXISTS face_weights (
  id           TEXT PRIMARY KEY,
  revision     TEXT NOT NULL,
  digest       TEXT NOT NULL,
  size_bytes   INTEGER NOT NULL,
  installed_at INTEGER NOT NULL
)
"""

_ATTRIBUTED_INDEX = (
    "CREATE INDEX IF NOT EXISTS ix_face_tracks_attributed"
    " ON face_tracks(attributed_at DESC, id DESC) WHERE person_id IS NOT NULL"
)

# A person's appearances, newest decision first; partial, as most faces have no person (v25).
_PERSON_RECENT_INDEX = (
    "CREATE INDEX IF NOT EXISTS ix_face_tracks_person_recent"
    " ON face_tracks(person_id, attributed_at, id) WHERE person_id IS NOT NULL"
)

# Faces a task found on a day, which Insights adds up by `created_at`.
_TRACKS_BY_DAY_INDEX = (
    "CREATE INDEX IF NOT EXISTS ix_face_tracks_created ON face_tracks(created_at)"
)

_PILES_BY_STATUS_INDEX = "CREATE INDEX IF NOT EXISTS ix_face_piles_status ON face_piles(status)"

_INDEXES = (
    _ATTRIBUTED_INDEX,
    _TRACKS_BY_DAY_INDEX,
    "CREATE INDEX IF NOT EXISTS ix_face_tracks_asset ON face_tracks(asset_id)",
    _PERSON_RECENT_INDEX,
    # A pile's faces; an identified face has left its pile.
    "CREATE INDEX IF NOT EXISTS ix_face_tracks_pile ON face_tracks(pile_id) "
    "WHERE pile_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_face_detections_track ON face_detections(track_id)",
    "CREATE INDEX IF NOT EXISTS ix_face_references_person ON face_references(person_id)",
    _INDEX_REFERENCE_TRACK,
    _INDEX_REFERENCE_ASSET,
    "CREATE INDEX IF NOT EXISTS ix_face_references_pack ON face_references(pack_id) "
    "WHERE pack_id IS NOT NULL",
    _PILES_BY_STATUS_INDEX,
)

# Everything this feature claimed about one person, which a re-match reconsiders.
_INDEX_CLAIMS_PERSON = (
    "CREATE INDEX IF NOT EXISTS ix_face_asset_people_person ON face_asset_people(person_id)"
)

# v28: "this group may be that person" from a folder, kept so the review list can ask; an answer
# stays so the pass never proposes it again. `files` and `of_files` are evidence, never shown.
_CREATE_PILE_PROPOSALS = """
CREATE TABLE IF NOT EXISTS face_pile_proposals (
  pile_id    TEXT NOT NULL REFERENCES face_piles(id) ON DELETE CASCADE,
  person_id  TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  reason     TEXT NOT NULL CHECK(reason IN ('folder')),
  folder_id  TEXT NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
  files      INTEGER NOT NULL,
  of_files   INTEGER NOT NULL,
  state      TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','refused','accepted')),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY(pile_id, person_id)
)
"""

# The pass asks by folder and person to take back a proposal.
_INDEX_PILE_PROPOSALS_FOLDER = (
    "CREATE INDEX IF NOT EXISTS ix_face_pile_proposals_folder "
    "ON face_pile_proposals(folder_id, person_id)"
)


# What a pack brought that could not be placed, held until somebody is recognized; deleting the
# person puts the entry back.
_CREATE_PACK_ENTRIES = """
CREATE TABLE IF NOT EXISTS pack_entries (
  id                TEXT PRIMARY KEY,
  pack_id           TEXT NOT NULL REFERENCES face_packs(id) ON DELETE CASCADE,
  name              TEXT NOT NULL,
  aliases           TEXT NOT NULL DEFAULT '[]',
  links             TEXT NOT NULL DEFAULT '[]',
  claimed_person_id TEXT REFERENCES people(id) ON DELETE SET NULL,
  created_at        INTEGER NOT NULL,
  declined_at       INTEGER,
  source            TEXT,
  confirmed         INTEGER,
  UNIQUE(pack_id, name COLLATE NOCASE)
)
"""

# The faces under an unclaimed entry, stored as references are, keyed so re-importing adds nothing.
_CREATE_PACK_ENTRY_FACES = """
CREATE TABLE IF NOT EXISTS pack_entry_faces (
  id          TEXT PRIMARY KEY,
  entry_id    TEXT NOT NULL REFERENCES pack_entries(id) ON DELETE CASCADE,
  crop_path   TEXT,
  crop_digest TEXT NOT NULL,
  embedding   BLOB NOT NULL,
  quality     REAL NOT NULL,
  recognizer  TEXT NOT NULL,
  turned      INTEGER NOT NULL DEFAULT 0,
  UNIQUE(entry_id, crop_digest)
)
"""

# What the last folder import left out, by file, so a rerun adds nothing.
_CREATE_FOLDER_LEFT_OUT = """
CREATE TABLE IF NOT EXISTS face_folder_left_out (
  job_id TEXT NOT NULL,
  file   TEXT NOT NULL,
  reason TEXT NOT NULL,
  PRIMARY KEY (job_id, file)
)
"""

# Each picture a folder import read, so a restarted import skips a finished folder.
_CREATE_FOLDER_READ = """
CREATE TABLE IF NOT EXISTS face_folder_read (
  pack_id    TEXT NOT NULL REFERENCES face_packs(id) ON DELETE CASCADE,
  recognizer TEXT NOT NULL,
  folder     TEXT NOT NULL COLLATE NOCASE,
  digest     TEXT NOT NULL,
  PRIMARY KEY (pack_id, recognizer, folder, digest)
)
"""

_PACK_ENTRY_INDEXES = (
    # Unplaced entries by name; partial, as most are claimed.
    "CREATE INDEX IF NOT EXISTS ix_pack_entries_unclaimed ON pack_entries(name COLLATE NOCASE)"
    " WHERE claimed_person_id IS NULL",
    "CREATE INDEX IF NOT EXISTS ix_pack_entry_faces_entry ON pack_entry_faces(entry_id)",
)

# Every table, piles before tracks because a track names one.
_TABLES = (
    _CREATE_SCANS,
    _CREATE_PILES,
    _CREATE_TRACKS,
    _CREATE_DETECTIONS,
    _CREATE_PACKS,
    _CREATE_REFERENCES,
    _CREATE_REJECTIONS,
    _CREATE_WEIGHTS,
    _CREATE_CLAIMS,
    _CREATE_PACK_ENTRIES,
    _CREATE_PACK_ENTRY_FACES,
    _CREATE_REMOVED,
    _CREATE_IGNORED,
    _CREATE_CONFIRMATIONS,
    _CREATE_GROUPING,
    _CREATE_RUNS,
    _CREATE_REJECTED,
    _CREATE_PILE_PROPOSALS,
    _CREATE_SUCCESSORS,
    _CREATE_STARTER_REFUSALS,
    _CREATE_FOLDER_LEFT_OUT,
    _CREATE_OWN_LIBRARY,
    _CREATE_FOLDER_READ,
)

_MORE_INDEXES = (
    _INDEX_PACKS_LIBRARY,
    _INDEX_CLAIMS_PERSON,
    *_PACK_ENTRY_INDEXES,
    _INDEX_REMOVED_ASSET,
    _INDEX_IGNORED_ASSET,
    _INDEX_CONFIRMED_ASSET,
    _INDEX_CONFIRMED_PERSON,
    _INDEX_GROUPING_ASSET,
    _INDEX_REJECTED_PERSON,
    _INDEX_REJECTED_ASSET,
    _INDEX_PILE_PROPOSALS_FOLDER,
    _INDEX_SUCCESSORS_SUCCESSOR,
    _INDEX_SUCCESSORS_ASSET,
)

#: Version 36's step: the words a question's asker may be, before and after a stash-box joined.
_ASKED_BY_WAS = "asked_by IN ('match','group','undone')"
_ASKED_BY_NOW = "asked_by IN ('match','group','undone','box')"

#: Version 37's step: a reference may be a face Sift recognized (`Origin.RECOGNIZED`).
_ORIGIN_WAS = "origin IN ('added','pack','confirmed','seed')"
_ORIGIN_NOW = "origin IN ('added','pack','confirmed','seed','recognized')"

#: Version 38's step: an entry whose person an Undo took back.
_ENTRY_DECLINED = "ALTER TABLE pack_entries ADD COLUMN declined_at INTEGER"

#: Version 39's step: an entry's own folder and its confirmed count.
_ENTRY_SOURCE_AND_COUNT = {
    "source": "ALTER TABLE pack_entries ADD COLUMN source TEXT",
    "confirmed": "ALTER TABLE pack_entries ADD COLUMN confirmed INTEGER",
}

#: Version 41's step: the library a pack came from.
_PACKS_LIBRARY = "ALTER TABLE face_packs ADD COLUMN library TEXT"

#: Version 44's step: a held face turned past the quality bar's angle.
_ENTRY_FACE_TURNED = "ALTER TABLE pack_entry_faces ADD COLUMN turned INTEGER NOT NULL DEFAULT 0"

#: Version 34's step. See `initialize`.
_INDEXES_A_LIBRARY_MAY_LACK = (_TRACKS_BY_DAY_INDEX, _PILES_BY_STATUS_INDEX)

#: Version 35's step: the refusal split by reason, and the size of the biggest face too small.
_REFUSAL_REASONS = {
    "refused_largest": "ALTER TABLE face_scans ADD COLUMN refused_largest INTEGER",
    "refused_blurred": "ALTER TABLE face_scans ADD COLUMN refused_blurred INTEGER",
    "refused_turned": "ALTER TABLE face_scans ADD COLUMN refused_turned INTEGER",
    "refused_edge": "ALTER TABLE face_scans ADD COLUMN refused_edge INTEGER",
}


# A foreign key's parent is resolved when a row is written, so the components' order is free.
async def initialize(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        for statement in (*_TABLES, *_INDEXES, *_MORE_INDEXES):
            await connection.execute(statement)
    # Version 34: two indexes a library brought up through the old steps never received.
    if 0 < on_disk < 34:
        for statement in _INDEXES_A_LIBRARY_MAY_LACK:
            await connection.execute(statement)
    # Version 35: the refusal reasons, NULL on older rows.
    if 0 < on_disk < 35:
        await _add_columns(connection, "face_scans", _REFUSAL_REASONS)
    # Version 36: only the stored CHECK widens, so no row moves.
    if 0 < on_disk < 36 and not await check_allows(connection, "face_tracks", "box"):
        await widen_a_check(connection, "face_tracks", was=_ASKED_BY_WAS, now=_ASKED_BY_NOW)
    # Version 37: as version 36.
    if 0 < on_disk < 37 and not await check_allows(connection, "face_references", "recognized"):
        await widen_a_check(connection, "face_references", was=_ORIGIN_WAS, now=_ORIGIN_NOW)
    if 0 < on_disk < 38:
        await _add_columns(connection, "pack_entries", {"declined_at": _ENTRY_DECLINED})
    if 0 < on_disk < 39:
        await _add_columns(connection, "pack_entries", _ENTRY_SOURCE_AND_COUNT)
    await _steps_from_40(connection, on_disk)


async def _steps_from_40(connection: Connection, on_disk: int) -> None:
    if 0 < on_disk < 40:
        await connection.execute(_CREATE_FOLDER_LEFT_OUT)
    if 0 < on_disk < 41:
        await _add_columns(connection, "face_packs", {"library": _PACKS_LIBRARY})
        await connection.execute(_INDEX_PACKS_LIBRARY)
        await connection.execute(_CREATE_OWN_LIBRARY)
    if on_disk < 42:
        for statement in _MARKS:
            await connection.execute(statement)
    if 0 < on_disk < 43:
        await connection.execute(_CREATE_FOLDER_READ)
    if 0 < on_disk < 44:
        await _add_columns(connection, "pack_entry_faces", {"turned": _ENTRY_FACE_TURNED})


_MARKS = marks(
    "face_scans",
    watched=(
        "asset_id",
        "coverage",
        "quality_version",
        "sampling_version",
        "settings_digest",
        "settings_shape",
        "settings_density",
    ),
)


async def _add_columns(connection: Connection, table: str, columns: dict[str, str]) -> None:
    """Add each column the table lacks, so a step taken twice is taken once."""
    for column, statement in columns.items():
        if not await column_exists(connection, table, column):
            await connection.execute(statement)


register_schema_initializer(COMPONENT, VERSION, initialize, depends_on=["content"], baseline=33)


# The faces still waiting for a name, counted per user and group by the kernel's visibility layer.
register_counted(
    Counted(
        WAITING_FACES_KIND,
        "(SELECT asset_id, pile_id FROM face_tracks WHERE person_id IS NULL AND pile_id IS NOT NULL)",
        "pile_id",
        "face_tracks",
        "UPDATE OF asset_id, person_id, pile_id",
        keys=(("id",),),
    ),
    component=COMPONENT,
)


#: The kind under which a person's attributed faces are counted per user, by person and band.
FACE_BAND_KIND = "face_band"

#: Between the person and the band in the object id; neither half can hold it.
FACE_BAND_SEPARATOR = ":"

#: A face with a person on it, as (file, person and band), the separator written into the text.
_FACE_BAND_SOURCE = (
    "(SELECT asset_id, person_id || ':' || COALESCE(attribution, '') AS band"
    " FROM face_tracks WHERE person_id IS NOT NULL)"
)
if f"|| '{FACE_BAND_SEPARATOR}' ||" not in _FACE_BAND_SOURCE:  # pragma: no cover (an edit)
    raise RuntimeError("the face band source and its separator disagree")

# Kept by the same recompute as every count, so the vault holds it back as it holds the files.
register_counted(
    Counted(
        FACE_BAND_KIND,
        _FACE_BAND_SOURCE,
        "band",
        "face_tracks",
        "UPDATE OF asset_id, person_id, attribution",
        keys=(("id",),),
    ),
    component=COMPONENT,
)
