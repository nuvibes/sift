# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tables a file's identity lives in: `library` (where media sits) and `content` (what it is).

`content` depends on `library`, as SQLite would only complain about a missing parent at insert."""

from __future__ import annotations

from sift.kernel.content import backlog
from sift.kernel.db import Connection, register_schema_initializer
from sift.kernel.migrations import column_exists

LIBRARY_COMPONENT = "library"
LIBRARY_VERSION = 7

CONTENT_COMPONENT = "content"
CONTENT_VERSION = 32

USER_STATE_COMPONENT = "user_state"
USER_STATE_VERSION = 6

#: The sort key stored beside each file name, held by `tests/gates/test_one_ordering.py`.
CONTENT_SORT_KEYS: tuple[tuple[str, str, str], ...] = (
    ("assets", "original_filename", "filename_sort"),
)

#: What a feature could not make for a file, and why, one row per file and product; `transient`
#: marks a moment (a share away) that the next scan clears.
_CREATE_FILE_VERDICTS = """
CREATE TABLE IF NOT EXISTS file_verdicts (
  asset_id   TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  product    TEXT NOT NULL,
  code       TEXT NOT NULL,
  reason     TEXT NOT NULL,
  transient  INTEGER NOT NULL DEFAULT 0,
  at         INTEGER NOT NULL,
  PRIMARY KEY (asset_id, product)
)
"""

#: The count per product the Build sheet draws, and the exclusion every lacking predicate makes.
_CREATE_FILE_VERDICTS_INDEX = (
    "CREATE INDEX IF NOT EXISTS ix_file_verdicts_product ON file_verdicts(product, transient)"
)
#: What ffprobe said about a file, compressed with every place-naming key stripped, kept so a new
#: field is a migration rather than a pass over the library.
_CREATE_ASSET_PROBES = """
CREATE TABLE IF NOT EXISTS asset_probes (
  asset_id      TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
  probe_version INTEGER NOT NULL,
  tool          TEXT NOT NULL,
  body          BLOB NOT NULL,
  probed_at     INTEGER NOT NULL
)
"""

#: What a user thought, as it changed, since the state row keeps only today's value. No foreign
#: key on the subject, as a deleted file stays in the history; the user's cascades.
_CREATE_OPINIONS = """
CREATE TABLE IF NOT EXISTS opinions (
  id           TEXT PRIMARY KEY,
  user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  subject_kind TEXT NOT NULL,
  subject_id   TEXT NOT NULL,
  kind         TEXT NOT NULL CHECK (kind IN ('rating','favorite','pin','o','hide')),
  before       INTEGER,
  after        INTEGER,
  at           INTEGER NOT NULL
)
"""

#: One user's history in time order, and one subject's.
_OPINION_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_opinions_user ON opinions(user_id, at)",
    "CREATE INDEX IF NOT EXISTS ix_opinions_subject ON opinions(subject_kind, subject_id, at)",
)

# No consent flag for changing files (the confirmation protects them) and no per-root opt-out
# of the watcher (one library-wide setting).
_CREATE_LIBRARY_ROOTS = """
CREATE TABLE IF NOT EXISTS library_roots (
  id                  TEXT PRIMARY KEY,
  name                TEXT NOT NULL,
  abs_path            TEXT NOT NULL UNIQUE,
  kind                TEXT NOT NULL DEFAULT 'local' CHECK(kind IN ('local','nas','other')),
  created_at          INTEGER NOT NULL
)
"""

# A folder handed over through the system's own dialog, which no page can drive: the picker's
# confinement. Permission to look only; changing is the filesystem's answer at the write.
_CREATE_BROWSE_GRANTS = """
CREATE TABLE IF NOT EXISTS browse_grants (
  id         TEXT PRIMARY KEY,
  abs_path   TEXT NOT NULL UNIQUE,
  granted_at INTEGER NOT NULL
)
"""

# `seen_mtime` makes the start catch-up cost folders rather than files; NULL means never recorded,
# read as cannot be ruled out. A file rewritten in place does not move it (see `reconcile`).
_CREATE_FOLDERS = """
CREATE TABLE IF NOT EXISTS folders (
  id         TEXT PRIMARY KEY,
  root_id    TEXT NOT NULL REFERENCES library_roots(id) ON DELETE CASCADE,
  parent_id  TEXT REFERENCES folders(id) ON DELETE CASCADE,
  rel_path   TEXT NOT NULL,
  name       TEXT NOT NULL,
  seen_mtime REAL,
  UNIQUE(root_id, rel_path)
)
"""

_LIBRARY_INDEXES = (
    # The folder tree walk every folder access check makes.
    "CREATE INDEX IF NOT EXISTS ix_folders_parent ON folders(parent_id)",
)


# Hiding a library or a folder, per user (screen protection, not access), a table each so
# cascades from both sides clean up; sparse, an absent row reads as not hidden.
_CREATE_ROOT_USER_STATE = """
CREATE TABLE IF NOT EXISTS root_user_state (
  root_id    TEXT NOT NULL REFERENCES library_roots(id) ON DELETE CASCADE,
  user_id    TEXT NOT NULL REFERENCES users(id)         ON DELETE CASCADE,
  hidden     INTEGER NOT NULL DEFAULT 0,
  hidden_at  INTEGER,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (root_id, user_id)
)
"""

_CREATE_FOLDER_USER_STATE = """
CREATE TABLE IF NOT EXISTS folder_user_state (
  folder_id  TEXT NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
  user_id    TEXT NOT NULL REFERENCES users(id)   ON DELETE CASCADE,
  hidden     INTEGER NOT NULL DEFAULT 0,
  hidden_at  INTEGER,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (folder_id, user_id)
)
"""

_LIBRARY_STATE_INDEXES = (
    # One user's hidden set, partial as most rows are not hidden.
    "CREATE INDEX IF NOT EXISTS ix_rus_hidden ON root_user_state(user_id, hidden) WHERE hidden = 1",
    "CREATE INDEX IF NOT EXISTS ix_fus_hidden ON folder_user_state(user_id, hidden)"
    " WHERE hidden = 1",
)

# CHECKs repeat the enums, as SQLite takes no placeholder there; tests compare them. A NULL
# probe column means not read yet, never zero.
_CREATE_ASSETS = """
CREATE TABLE IF NOT EXISTS assets (
  id                  TEXT PRIMARY KEY,
  -- What the bytes are: the sampled identity, or the whole-file digest on a row still waiting to be
  -- brought forward (`identity_version = 0`). `whole_digest` is the digest of every byte, kept
  -- where it was taken.
  identity            TEXT NOT NULL UNIQUE,
  media_type          TEXT NOT NULL CHECK(media_type IN ('video','image','gif')),
  mime                TEXT,
  width               INTEGER,
  height              INTEGER,
  duration_ms         INTEGER,
  -- The second half of whether this machine can transcode a file faster than it plays.
  fps                 REAL,
  size_bytes          INTEGER,
  container           TEXT,
  vcodec              TEXT,
  acodec              TEXT,
  -- How many bits each colour sample carries: eight for most files, ten for HDR and the higher
  -- profiles of AV1 and HEVC.
  bit_depth           INTEGER,
  phash               TEXT,
  videohash           TEXT,
  original_filename   TEXT,
  -- The order a file name is read in (`kernel.sorting.sort_key`). Every ORDER BY falls back to the
  -- name, so a row whose key has not been written is in its old place rather than missing.
  filename_sort       TEXT,
  added_at            INTEGER NOT NULL,
  probed_at           INTEGER,
  -- How far this file stores its audio from its video for the same moment, in bytes. NULL is not
  -- measured, zero is nothing to get wrong, anything else the worst distance measured.
  interleave_gap      INTEGER,
  -- The exact-file fingerprint the wider world files a video under, and the one that survives a
  -- re-encode, sampled across the WHOLE video.
  oshash              TEXT,
  video_phash         TEXT,
  title               TEXT,
  -- Where SIFT fetched this copy from, as a field somebody can correct. Not the downloads ledger's
  -- address, which is hashed into the key that recognises a re-dropped link.
  download_url        TEXT,
  -- When the thing in the file was published, as an ISO date. NULL means nobody has said, never
  -- the day it arrived.
  release_date        TEXT,
  identity_version    INTEGER NOT NULL DEFAULT 0,
  whole_digest        TEXT,
  -- How the picture's brightness is encoded, as ffprobe names it: the one fact that says whether a
  -- file is HDR. Empty for a stream that does not say.
  color_transfer      TEXT,
  -- When a file's last place went with a removed library folder, so the record is left alone for a
  -- while and adding the folder again brings everything back.
  stranded_at         INTEGER,
  -- Which generation of work wrote the four near-duplicate fingerprints, so an improvement is a
  -- pass over the rows below the number rather than a decode of every video.
  fingerprint_version INTEGER,
  audio_channels      INTEGER,
  audio_sample_rate   INTEGER,
  -- How long the PICTURE runs, beside how long the file runs. ZERO is a reading that names none,
  -- which the samplers read as "use the file's".
  video_duration_ms   INTEGER,
  -- Which generation of the ingress classifier decided `media_type` and `mime`. See
  -- `ingress.CLASSIFIER_VERSION`.
  classified_version  INTEGER NOT NULL DEFAULT 1,
  -- What a stash-box says about a RELEASE: what it is about, when it was shot (not when it came
  -- out), and the reference the Site that released it files it under.
  details             TEXT,
  production_date     TEXT,
  site_code           TEXT,
  -- The track a file is set to: one line of text, an artist and a song as written wherever it
  -- came from, stored as it arrived and indexed as words.
  music               TEXT,
  -- Kept local: nothing about this file is sent outside this machine.
  keep_local          INTEGER NOT NULL DEFAULT 0,
  -- Do not swap: a swap never offers this file, and never says this library holds it.
  keep_from_swaps     INTEGER NOT NULL DEFAULT 0,
  -- The moment the tile's still was cut at, chosen by what the frame shows (a black or faded
  -- frame is refused). NULL is a still not cut yet, or cut before that choice existed; either is
  -- read as the first frame.
  still_at_ms         INTEGER,
  -- A JPEG a browser draws apart from its stored pixels: NULL never looked, 0 alike, 1 apart.
  turn_apart          INTEGER
)
"""

_CREATE_ASSET_LOCATIONS = """
CREATE TABLE IF NOT EXISTS asset_locations (
  id            TEXT PRIMARY KEY,
  asset_id      TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  root_id       TEXT NOT NULL REFERENCES library_roots(id) ON DELETE CASCADE,
  folder_id     TEXT REFERENCES folders(id) ON DELETE SET NULL,
  rel_path      TEXT NOT NULL,
  filename      TEXT NOT NULL,
  size_bytes    INTEGER,
  mtime         INTEGER,
  status        TEXT NOT NULL DEFAULT 'present' CHECK(status IN ('present','missing')),
  first_seen_at INTEGER NOT NULL,
  last_seen_at  INTEGER NOT NULL,
 -- Where this picture sits when it is inside an archive rather than being a file of its own.
 -- Both NULL for every ordinary file, which is nearly everything. `rel_path` above is still the
 -- one unique name for the location (for a member it is the archive's path with the member's
 -- name under it), so nothing about the uniqueness rule changes because of these two.
 --
 -- Kept as two columns rather than picked back out of `rel_path`, because a path split on a
 -- separator is a guess whenever the archive's own name contains one.
  archive_rel_path TEXT,
  member_path      TEXT,
  UNIQUE(root_id, rel_path)
)
"""

# `params` is NOT NULL so UNIQUE dedups, as SQLite treats NULLs as distinct; `content_hash` lets a
# browser keep a copy; `recipe_version` is a column so a bump leaves addresses intact.
_CREATE_DERIVATIVES = """
CREATE TABLE IF NOT EXISTS derivatives (
  id             TEXT PRIMARY KEY,
  asset_id       TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  kind           TEXT NOT NULL CHECK(kind IN ('thumb','preview','sprite','rendition','remux')),
  rel_cache_path TEXT NOT NULL,
  params         TEXT NOT NULL DEFAULT '{}',
  size_bytes     INTEGER,
  content_hash   TEXT,
  created_at     INTEGER NOT NULL,
  recipe_version INTEGER NOT NULL DEFAULT 0,
  UNIQUE(asset_id, kind, params)
)
"""

# The grid's orders, each ending in the id tiebreaker; the name index is on the ordered expression.
_SORT_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_assets_added_id ON assets(added_at, id)",
    "CREATE INDEX IF NOT EXISTS ix_assets_name"
    " ON assets(COALESCE(filename_sort, original_filename), id)",
    "CREATE INDEX IF NOT EXISTS ix_assets_duration ON assets(duration_ms, id)",
    "CREATE INDEX IF NOT EXISTS ix_assets_size ON assets(size_bytes, id)",
)

#: The files kept out of swaps, partial like `keep_local`.
_INDEX_KEPT_FROM_SWAPS = (
    "CREATE INDEX IF NOT EXISTS ix_assets_kept_from_swaps ON assets(id) WHERE keep_from_swaps = 1"
)

#: Content version 30: indexes making each start question a seek rather than a walk.
_START_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_assets_unread ON assets(added_at, id) WHERE probed_at IS NULL",
    "CREATE INDEX IF NOT EXISTS ix_assets_classified ON assets(classified_version)",
    "CREATE INDEX IF NOT EXISTS ix_assets_video_unhashed ON assets(id)"
    " WHERE media_type = 'video' AND oshash IS NULL",
    "CREATE INDEX IF NOT EXISTS ix_assets_print_version ON assets(fingerprint_version)",
)

#: Content version 31: the passes' counts as rows, and the marks each file write leaves for them.
_BACKLOG = (
    *backlog.TABLES,
    *backlog.marks("assets", "id"),
    *backlog.marks("asset_locations", watched=("status", "asset_id", "root_id")),
    *backlog.marks("derivatives"),
    *backlog.marks("file_verdicts"),
)

_CONTENT_INDEXES = (
    # Near-duplicate search sorts on this.
    "CREATE INDEX IF NOT EXISTS ix_assets_phash ON assets(phash)",
    *_SORT_INDEXES,
    # Only rows still waiting to be re-identified.
    "CREATE INDEX IF NOT EXISTS ix_assets_legacy_identity"
    " ON assets(identity_version) WHERE identity_version = 0",
    # The files kept local, partial on the one value asked.
    "CREATE INDEX IF NOT EXISTS ix_assets_kept_local ON assets(id) WHERE keep_local = 1",
    _INDEX_KEPT_FROM_SWAPS,
    # Hover clips of another recipe, counted without touching the table.
    "CREATE INDEX IF NOT EXISTS ix_derivatives_preview ON derivatives(asset_id, params)"
    " WHERE kind = 'preview'",
    # Every access check reads an asset's locations.
    "CREATE INDEX IF NOT EXISTS ix_loc_asset ON asset_locations(asset_id)",
    # A folder's contents: the file browser.
    "CREATE INDEX IF NOT EXISTS ix_loc_folder ON asset_locations(folder_id)",
    # No index on `derivatives(asset_id)`: the UNIQUE index already leads with it.
)


# One row per person per asset touched, absent reading as defaults; a cleared rating is NULL.
_CREATE_ASSET_USER_STATE = """
CREATE TABLE IF NOT EXISTS asset_user_state (
  asset_id       TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  user_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  favorite       INTEGER NOT NULL DEFAULT 0,
  rating         INTEGER CHECK(rating IS NULL OR rating BETWEEN 1 AND 10),
  view_count     INTEGER NOT NULL DEFAULT 0,
  watched_ms     INTEGER NOT NULL DEFAULT 0,
  resume_ms      INTEGER,
  hidden         INTEGER NOT NULL DEFAULT 0,
  hidden_at      INTEGER,
  last_viewed_at INTEGER,
  completed_at   INTEGER,
  pinned         INTEGER NOT NULL DEFAULT 0,
  o_count        INTEGER NOT NULL DEFAULT 0,
  updated_at     INTEGER NOT NULL,
  PRIMARY KEY (asset_id, user_id)
)
"""

# Which parts of a file each user played, a row per bucket incremented in SQL so two players
# on one clip lose nothing; buckets are fractions of the file, never times.
_CREATE_ASSET_REPLAY_HEAT = """
CREATE TABLE IF NOT EXISTS asset_replay_heat (
  asset_id   TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  bucket     INTEGER NOT NULL,
  watched_ms INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (asset_id, user_id, bucket)
)
"""

_USER_STATE_INDEXES = (
    # The history rail, partial as most rows were never played.
    "CREATE INDEX IF NOT EXISTS ix_aus_recent ON asset_user_state(user_id, last_viewed_at DESC)"
    " WHERE last_viewed_at IS NOT NULL",
    # One person's favorites.
    "CREATE INDEX IF NOT EXISTS ix_aus_favorite ON asset_user_state(user_id, favorite)"
    " WHERE favorite = 1",
    # One person's hidden files, partial for the same reason.
    "CREATE INDEX IF NOT EXISTS ix_aus_hidden ON asset_user_state(user_id, hidden)"
    " WHERE hidden = 1",
)


#: Content version 26: drops an index nothing reads now.
_DROP_STILLS_UNMEASURED_INDEX = "DROP INDEX IF EXISTS ix_assets_still_unmeasured"

#: Content version 29: HEIF stills were read as one tile; their rows are read again whole.
_REREAD_THE_HEIF_STILLS = (
    "UPDATE assets SET probed_at = NULL, phash = NULL WHERE media_type = 'image'"
    " AND COALESCE(mime, '') IN ('image/heic', 'image/avif')"
)

#: Content version 28: rows the classifier's second generation reads again, by MIME and extension.
_RECLASSIFY_THE_WEBP_AND_HEIF_ROWS = (
    "UPDATE assets SET classified_version = 2 WHERE classified_version = 1"
    " AND COALESCE(mime, '') NOT IN ('image/webp', 'image/avif', 'image/heic')"
    " AND lower(COALESCE(original_filename, '')) NOT LIKE '%.webp'"
    " AND lower(COALESCE(original_filename, '')) NOT LIKE '%.avif'"
    " AND lower(COALESCE(original_filename, '')) NOT LIKE '%.heic'"
    " AND lower(COALESCE(original_filename, '')) NOT LIKE '%.heif'"
)

#: Content version 27: animated AVIF hover clips cut from the still cover go, to be cut again.
_DROP_AVIF_PREVIEWS_CUT_FROM_THE_COVER = (
    "DELETE FROM derivatives WHERE kind = 'preview' AND asset_id IN "
    "(SELECT id FROM assets WHERE media_type = 'gif' AND mime = 'image/avif')"
)

#: Content version 32: whether a browser draws a JPEG apart from its stored pixels (NULL never
#: looked, 0 alike, 1 apart), written at the take-in from the head already read.
_ADD_TURN_APART = "ALTER TABLE assets ADD COLUMN turn_apart INTEGER"

#: Content version 25: the moment a tile's still was cut at.
_ADD_STILL_AT = "ALTER TABLE assets ADD COLUMN still_at_ms INTEGER"

#: Content version 24: "Do not swap" on a file, beside `keep_local`.
_ADD_KEPT_FROM_SWAPS = "ALTER TABLE assets ADD COLUMN keep_from_swaps INTEGER NOT NULL DEFAULT 0"

#: Content version 23: drops an index nothing reads now.
_DROP_UNDEPTHED_INDEX = "DROP INDEX IF EXISTS ix_assets_undepthed"


async def initialize_library(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        for statement in (
            _CREATE_LIBRARY_ROOTS,
            _CREATE_FOLDERS,
            *_LIBRARY_INDEXES,
            _CREATE_ROOT_USER_STATE,
            _CREATE_FOLDER_USER_STATE,
            *_LIBRARY_STATE_INDEXES,
            _CREATE_BROWSE_GRANTS,
        ):
            await connection.execute(statement)


_STEPS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (23, (_DROP_UNDEPTHED_INDEX,)),
    (24, (_ADD_KEPT_FROM_SWAPS, _INDEX_KEPT_FROM_SWAPS)),
    (25, (_ADD_STILL_AT,)),
    (26, (_DROP_STILLS_UNMEASURED_INDEX,)),
    (27, (_DROP_AVIF_PREVIEWS_CUT_FROM_THE_COVER,)),
    (28, (_RECLASSIFY_THE_WEBP_AND_HEIF_ROWS,)),
    (29, (_REREAD_THE_HEIF_STILLS,)),
    (30, _START_INDEXES),
    (31, _BACKLOG),
)


async def initialize_content(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        for statement in (
            _CREATE_ASSETS,
            _CREATE_ASSET_LOCATIONS,
            _CREATE_DERIVATIVES,
            *_CONTENT_INDEXES,
            _CREATE_FILE_VERDICTS,
            _CREATE_FILE_VERDICTS_INDEX,
            _CREATE_ASSET_PROBES,
            _CREATE_OPINIONS,
            *_OPINION_INDEXES,
            *_START_INDEXES,
            *_BACKLOG,
        ):
            await connection.execute(statement)
    for version, statements in _STEPS:
        if 0 < on_disk < version:
            for statement in statements:
                await connection.execute(statement)
    # Version 32; checked, since a library built at an earlier version by this tree's CREATE has it.
    if 0 < on_disk < 32 and not await column_exists(connection, "assets", "turn_apart"):
        await connection.execute(_ADD_TURN_APART)


async def initialize_user_state(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        for statement in (
            _CREATE_ASSET_USER_STATE,
            *_USER_STATE_INDEXES,
            _CREATE_ASSET_REPLAY_HEAT,
        ):
            await connection.execute(statement)


register_schema_initializer(LIBRARY_COMPONENT, LIBRARY_VERSION, initialize_library, baseline=7)
register_schema_initializer(
    CONTENT_COMPONENT,
    CONTENT_VERSION,
    initialize_content,
    depends_on=[LIBRARY_COMPONENT],
    baseline=22,
)
# `users` is named in a foreign key without a dependency, as its component sits above this one;
# SQLite resolves the parent at write time, and a test proves the cascade.
register_schema_initializer(
    USER_STATE_COMPONENT,
    USER_STATE_VERSION,
    initialize_user_state,
    depends_on=[CONTENT_COMPONENT],
    baseline=6,
)
