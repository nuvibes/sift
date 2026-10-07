# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tables a file's identity lives in.

Two components, because they are versioned separately and one depends on the other.

`library` is *where* media sits: the roots someone has pointed Sift at, and the folders inside
them. Folders are rows rather than path strings because they carry the access rules, and a
permission that lives in a string cannot be joined against.

`content` is *what* the media is. One `assets` row per unique BLAKE3 digest; one
`asset_locations` row per place those bytes physically sit; the `derivatives` built from them.
A path says where a file is, never what it is: rename it, move it to another disk, or keep a
second copy of it, and it is the same asset with another location.

`content` declares its dependency on `library`, so the roots and folders its foreign keys point
at exist before its own tables are created. SQLite will happily create a table whose parent is
missing and only complain at the first insert, which is a long way from the mistake.
"""

from __future__ import annotations

from sift.kernel.content import backlog
from sift.kernel.db import Connection, register_schema_initializer

LIBRARY_COMPONENT = "library"
LIBRARY_VERSION = 7

CONTENT_COMPONENT = "content"
CONTENT_VERSION = 31

USER_STATE_COMPONENT = "user_state"
USER_STATE_VERSION = 6

#: THE ORDER FILE NAMES ARE READ IN, stored beside the name: the content component's half of what
#: the catalog's `SORT_KEYS` declares for its own tables, as `(table, name column, key column)`.
#: Read by `tests/gates/test_one_ordering.py`, which refuses a statement that writes the name
#: without the key. See `kernel.sorting` for what the key is.
CONTENT_SORT_KEYS: tuple[tuple[str, str, str], ...] = (
    ("assets", "original_filename", "filename_sort"),
)

#: What a feature decided it could not make for a file, and why: one row per file and product.
#:
#: The one record every feature writes, so none answers the question its own way: a picture that
#: could not be cut is not offered by every Build for ever, a file none of whose moments decoded
#: is not recorded as looked at with no faces, a read that failed because a share was away is not
#: remembered as a refusal of the bytes, and a row the re-identifying pass cannot sample stops
#: keeping every import reading whole files. `transient` marks a verdict about a moment rather
#: than about the bytes (a share away, a file another program held), which the next scan of the
#: file clears; the rest stand until somebody asks for them to be tried again.
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
#: What ffprobe said about a file, kept.
#:
#: The probe reads the whole answer and keeps ten fields of it; everything else (the container's
#: own tags, the pixel format, the colour primaries, the profile and level, the bit rates) is
#: read and dropped. Every one of those has the same future: somebody needs it, and the only way
#: to get it is a pass over the library. Kept here it is a migration over rows that are already
#: here.
#:
#: `body` is the JSON, compressed, with every key that names a place stripped before it is stored
#: (see `media_jobs.ffmpeg.probe_body`, which is where that rule lives and is tested). One row
#: per asset, replaced by the next probe, and it goes with the file.
_CREATE_ASSET_PROBES = """
CREATE TABLE IF NOT EXISTS asset_probes (
  asset_id      TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
  probe_version INTEGER NOT NULL,
  tool          TEXT NOT NULL,
  body          BLOB NOT NULL,
  probed_at     INTEGER NOT NULL
)
"""

#: What a user thought, as it changed.
#:
#: A rating, a favourite, a pin, an O press and a concealment are a flag, a number and one shared
#: `updated_at` on `asset_user_state`: the write that sets today's value destroys the one before
#: it and keeps no time of its own. Nothing later can invent those moments, which is why the row
#: has to be written as they happen rather than when something wants to read them.
#:
#: NO foreign key on the subject, for the reason a play carries none: this is the user's own
#: history, and a file being deleted is part of that history rather than the end of it. The
#: user's key does cascade: a user removed takes what it thought with it, which is what the
#: deliberate forget means.
#:
#: `before` and `after` are plain integers whatever the kind: a rating is 1 to 10, a flag is 0 or
#: 1, an O press is the count either side. One shape, so a reader groups on `kind` and not on
#: which column to look in.
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

#: One user's history in time order, and one subject's. Both end in `at` because both are read
#: as a run of moments rather than as a set: a recap walks a year of one user, and a file's
#: page walks everything ever thought about that file.
_OPINION_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_opinions_user ON opinions(user_id, at)",
    "CREATE INDEX IF NOT EXISTS ix_opinions_subject ON opinions(subject_kind, subject_id, at)",
)

# Two columns this table deliberately does not carry:
#
# No consent flag for changing the files in a root (delete, rename, move): handing Sift a folder IS
# the permission, and what protects a file is the confirmation at the moment of a destructive act,
# which names the count and offers the two tiers. Whether the filesystem allows a write is still
# asked, at that moment, because that is a fact and not a flag.
#
# No per-root opt-out of the live watcher. How new files are noticed (told by the operating
# system, or polled on a timer) is one setting for the whole library, which is where the
# slow-share case is answered. Every root is watched.
_CREATE_LIBRARY_ROOTS = """
CREATE TABLE IF NOT EXISTS library_roots (
  id                  TEXT PRIMARY KEY,
  name                TEXT NOT NULL,
  abs_path            TEXT NOT NULL UNIQUE,
  kind                TEXT NOT NULL DEFAULT 'local' CHECK(kind IN ('local','nas','other')),
  created_at          INTEGER NOT NULL
)
"""

# A folder the person handed to Sift through the operating system's own dialog (a grant, not a
# mount).
#
# A GRANT IS NOT A LIBRARY ROOT, AND THE DIFFERENCE IS THE WHOLE SECURITY PROPERTY.
#
# Natively the backend runs as the logged-in user with access to every drive, so nothing outside
# Sift confines the folder picker the way a container's bind mounts would, and a bug in `confine`
# would reach everything while the code still looks correct.
#
# This table is the confinement. A grant is the record that a person opened Windows' own folder
# dialog (which no page can drive, read or reach) and chose this folder. The picker lists only
# what is inside a grant, so browsing is confined to folders somebody physically pointed at, and
# adding one requires the machine rather than a session.
#
# It carries no writable flag on purpose. A grant is permission to LOOK; whether Sift may CHANGE
# anything in a folder is the filesystem's answer, asked at the moment of the write.
_CREATE_BROWSE_GRANTS = """
CREATE TABLE IF NOT EXISTS browse_grants (
  id         TEXT PRIMARY KEY,
  abs_path   TEXT NOT NULL UNIQUE,
  granted_at INTEGER NOT NULL
)
"""

# `seen_mtime` is what makes catching up after a restart cost the number of FOLDERS rather than the
# number of files.
#
# A directory's own timestamp moves when an entry is added to it or taken out of it, on NTFS and
# on an SMB share alike, and on the share it is behind the redirector's attribute cache by a second
# or two, which does not matter for a question
# asked at startup about something that happened while the application was closed.
#
# So a scan records what it saw, and the pass at start compares. On a library of half a million
# files across five thousand folders that is five thousand `stat` calls, and a listing only of the
# folders that really changed, rather than half a million `stat` calls to discover that three
# files arrived. It does NOT move when a file already there is rewritten under the same name; see
# `reconcile` for why that case is left to the live watch and what it would cost to close.
#
# NULL means "never recorded", which every folder is until it is next walked, and which the pass
# reads as "cannot be ruled out" rather than as "unchanged".
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
    # Walking the folder tree, which the access check does on every request that names a folder.
    "CREATE INDEX IF NOT EXISTS ix_folders_parent ON folders(parent_id)",
)


# Hiding a library or a folder, for one user.
#
# ## Why these are per user at all
#
# Hiding is screen-share protection: it keeps something off YOUR screen, behind YOUR PIN. It is not
# an access rule: who may see a thing at all is decided by share and restrict, which the resolver
# applies separately. So the answer to "is this hidden" depends on who is asking, and a flag on the
# folder row cannot express that.
#
# ## Why a table each rather than one table for everything
#
# The same reason the entity state tables next door are separate: a foreign key cannot be
# polymorphic, and `ON DELETE CASCADE` from BOTH sides is what stops a deleted folder's rows
# outliving it and attaching themselves to whatever reuses the id. A shared table could only be kept
# tidy by remembering to sweep it by hand from every delete path.
#
# Sparse, like every other per-user table here: a row exists only where somebody hid something,
# and an absent row reads as not hidden.
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
    # One user's hidden set, which is what the Hidden screen lists. Partial, because the rows
    # nobody hid are most of the table and none of that query.
    "CREATE INDEX IF NOT EXISTS ix_rus_hidden ON root_user_state(user_id, hidden) WHERE hidden = 1",
    "CREATE INDEX IF NOT EXISTS ix_fus_hidden ON folder_user_state(user_id, hidden)"
    " WHERE hidden = 1",
)

# `media_type` repeats the values of `Kind`, and `derivatives.kind` repeats `DerivativeKind`.
# They have to: SQLite takes no placeholder in a CHECK constraint, and building the constraint
# from the enum would mean assembling SQL at runtime, which is the one thing the injection rule
# forbids. Tests read these constraints back out and compare them to the enums, so a drift is
# caught in the suite rather than by an insert failing months later.
#
# Every probe column is nullable and NULL means nobody has read it yet, never zero: filling one in
# means reading the file, which the probe does as files are touched.
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
  still_at_ms         INTEGER
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

# `params` is NOT NULL, defaulting to an empty object, and that is not cosmetic. SQLite treats
# every NULL as distinct from every other, so with a nullable column `UNIQUE(asset_id, kind,
# params)` would not dedup the commonest case at all: two thumbnails of one asset, both with no
# parameters, would insert happily as two rows. The column always holds canonical JSON.
#
# `content_hash` is the digest of the derivative's own bytes, short enough to sit in an address, so
# a browser may keep its copy without asking. NULL is a derivative whose file could not be read when
# it was written, which is served the careful way, revalidated on every use.
#
# `recipe_version` is which recipe a derivative was built to: a COLUMN and deliberately not another
# key in `params`, which is folded into the file's name on disk and into the unique key, so a
# version there would make every picture already built unreachable at its own address. Zero is
# "built before anybody counted", and a default means no writer can leave it out.
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

# The grid's orders. Each carries the id as its last column, because every order ends in the id
# as a tiebreaker and an index that stops one column short leaves the engine sorting the ties in
# a temporary tree. The name index is on the expression the order names, so the planner can match
# it: a plain index on the column is not used for an ORDER BY over COALESCE. A page by name, length
# or size then walks the index rather than sorting everything.
_SORT_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_assets_added_id ON assets(added_at, id)",
    "CREATE INDEX IF NOT EXISTS ix_assets_name"
    " ON assets(COALESCE(filename_sort, original_filename), id)",
    "CREATE INDEX IF NOT EXISTS ix_assets_duration ON assets(duration_ms, id)",
    "CREATE INDEX IF NOT EXISTS ix_assets_size ON assets(size_bytes, id)",
)

#: The files kept out of swaps: partial on the one value anybody looks for, as `keep_local` is.
_INDEX_KEPT_FROM_SWAPS = (
    "CREATE INDEX IF NOT EXISTS ix_assets_kept_from_swaps ON assets(id) WHERE keep_from_swaps = 1"
)

#: Content version 30: the rows a start and the duplicates screen ask about (unread, typed by an
#: older classifier, a video with no fingerprint or an older one's), so each is a seek on a library
#: that has none rather than a walk of every file.
_START_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_assets_unread ON assets(added_at, id) WHERE probed_at IS NULL",
    "CREATE INDEX IF NOT EXISTS ix_assets_classified ON assets(classified_version)",
    "CREATE INDEX IF NOT EXISTS ix_assets_video_unhashed ON assets(id)"
    " WHERE media_type = 'video' AND oshash IS NULL",
    "CREATE INDEX IF NOT EXISTS ix_assets_print_version ON assets(fingerprint_version)",
)

#: Content version 31: the passes' counts kept as rows, and the marks every write of a file leaves
#: for them (see `backlog`). A location moves a count only by its place, its file or its presence.
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
    # Only the rows still waiting to be re-identified, so "does any remain" is one probe of an
    # index that is empty on every library that has finished.
    "CREATE INDEX IF NOT EXISTS ix_assets_legacy_identity"
    " ON assets(identity_version) WHERE identity_version = 0",
    # The files kept local. Partial, on the one value anybody looks for: the facet and the outbound
    # door ask it, and nobody asks it of the zeros.
    "CREATE INDEX IF NOT EXISTS ix_assets_kept_local ON assets(id) WHERE keep_local = 1",
    _INDEX_KEPT_FROM_SWAPS,
    # The hover clips of another recipe, read without touching the table: the count a start asks
    # before rebuilding the previews (`identity._PREVIEWS_OF_ANOTHER_RECIPE`).
    "CREATE INDEX IF NOT EXISTS ix_derivatives_preview ON derivatives(asset_id, params)"
    " WHERE kind = 'preview'",
    # An asset's locations, which every access check needs: an asset is only as visible as the
    # least visible place it sits.
    "CREATE INDEX IF NOT EXISTS ix_loc_asset ON asset_locations(asset_id)",
    # A folder's contents, which is the file browser.
    "CREATE INDEX IF NOT EXISTS ix_loc_folder ON asset_locations(folder_id)",
    # No index on `derivatives(asset_id)`: the table carries UNIQUE(asset_id, kind, params), whose
    # own index starts with the same column, so every lookup (by asset, by asset and kind, and the
    # serving path's asset-kind-params) is answered by that one. A second index would cost a
    # second B-tree written on every derivative recorded, which is once per thumbnail, preview and
    # sprite of every file in the library.
)


# One row per person per asset they have touched, and none at all for the rest: a library of
# fifty thousand files where six have been rated is six rows, not fifty thousand mostly-empty
# ones. An absent row reads as all-defaults, which is what makes that work.
#
# `rating` is nullable and has no zero: clearing a rating is NULL, so a query for "rated at all"
# is a NULL check rather than a magic number somebody has to remember.
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

# Which PARTS of a file each user has played, and how long each part was on screen.
#
# `asset_user_state` beside it holds one number for the whole file: watch a ten-minute video twice
# and it reads twenty minutes, with nothing to say whether that was the same two minutes ten times
# or the whole thing twice. This table is that missing detail, and it is what draws the replay curve
# under a scrubber.
#
# ## Why a row per bucket rather than one row holding an array
#
# The tempting shape is one row per asset per user carrying a packed list of counters, read and
# written whole. It is not used, for the reason written over `_RECORD_VIEW`: a list can only be
# merged by reading it, adding to it and writing it back, and two players on one clip (which the
# player explicitly supports, see its `abLoop.watch`) would interleave and lose one of the two.
# A row per bucket is incremented by SQL itself, atomically, the way the view counter already is.
#
# The row count that shape appears to cost is mostly imaginary. A sitting writes only the buckets it
# actually played, so a ten-second look at a two-hour film writes one row, not a hundred; and a
# library is overwhelmingly files nobody has opened, which have no rows here at all.
#
# `bucket` is an index into a fixed number of equal slices of the file, NOT a time. That is what
# makes the table survive a file being replaced by a longer encode of itself: the curve still
# describes the same fractions of the same content, where stored milliseconds would silently point
# at the wrong moments.
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
    # The history rail: one person's most recently watched, newest first. Partial, because the
    # rows that have never been played are most of the table and none of this query.
    "CREATE INDEX IF NOT EXISTS ix_aus_recent ON asset_user_state(user_id, last_viewed_at DESC)"
    " WHERE last_viewed_at IS NOT NULL",
    # One person's favorites, which is a screen of its own.
    "CREATE INDEX IF NOT EXISTS ix_aus_favorite ON asset_user_state(user_id, favorite)"
    " WHERE favorite = 1",
    # And one person's hidden files, which is the Hidden screen. Partial for the same reason.
    "CREATE INDEX IF NOT EXISTS ix_aus_hidden ON asset_user_state(user_id, hidden)"
    " WHERE hidden = 1",
)


#: Content version 26: the partial index a one-time pass over the stills cut before the still was
#: chosen by what the frame shows walked, which nothing reads now. Every row written paid for it.
_DROP_STILLS_UNMEASURED_INDEX = "DROP INDEX IF EXISTS ix_assets_still_unmeasured"

#: Content version 29: every HEIF still (an iPhone HEIC, an AVIF still) was read through ffmpeg,
#: which reports one TILE of a grid picture as the frame, so its size, its still, its faces, its
#: description and its fingerprints came from one tile. The HEIF door reads the whole picture now;
#: these rows are read again: the probe runs on a row with no `probed_at`, and the fingerprints
#: follow a cleared `phash`. The faces and description passes take the same rows again through
#: their own catch-ups, which read the kind and the mime.
_REREAD_THE_HEIF_STILLS = (
    "UPDATE assets SET probed_at = NULL, phash = NULL WHERE media_type = 'image'"
    " AND COALESCE(mime, '') IN ('image/heic', 'image/avif')"
)

#: Content version 28: the rows the classifier's second generation reads again (see
#: `ingress.CLASSIFIER_VERSION`): every WebP, AVIF and HEIF row stays below the line, whatever kind
#: it was filed as, and every other row is stamped with the new number. Picked by MIME and by the
#: file name's extension both, because the rows in question are the ones an older gate got wrong,
#: and a row it got wrong can be wrong in either. A header read each, four kilobytes, never a decode.
_RECLASSIFY_THE_WEBP_AND_HEIF_ROWS = (
    "UPDATE assets SET classified_version = 2 WHERE classified_version = 1"
    " AND COALESCE(mime, '') NOT IN ('image/webp', 'image/avif', 'image/heic')"
    " AND lower(COALESCE(original_filename, '')) NOT LIKE '%.webp'"
    " AND lower(COALESCE(original_filename, '')) NOT LIKE '%.avif'"
    " AND lower(COALESCE(original_filename, '')) NOT LIKE '%.heic'"
    " AND lower(COALESCE(original_filename, '')) NOT LIKE '%.heif'"
)

#: Content version 27: the hover clip of an animated AVIF was cut from the file's still cover (its
#: first video stream) and held one frame for a second. Those clips go, so the Generate pass cuts
#: them again from the stream that moves; the still and every other kind of picture stay.
_DROP_AVIF_PREVIEWS_CUT_FROM_THE_COVER = (
    "DELETE FROM derivatives WHERE kind = 'preview' AND asset_id IN "
    "(SELECT id FROM assets WHERE media_type = 'gif' AND mime = 'image/avif')"
)

#: Content version 25: the moment a tile's still was cut at (see the column).
_ADD_STILL_AT = "ALTER TABLE assets ADD COLUMN still_at_ms INTEGER"

#: Content version 24: "Do not swap" on a file, beside `keep_local` (see the column).
_ADD_KEPT_FROM_SWAPS = "ALTER TABLE assets ADD COLUMN keep_from_swaps INTEGER NOT NULL DEFAULT 0"

#: Content version 23: the partial index a finished colour-depth sweep read, which nothing reads now.
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
# It names `users` in a foreign key but does not declare a dependency on the component that creates
# that table, and the omission is deliberate. That component belongs to the access layer, which is
# built on top of this one: declaring it here would mean this layer could not have its schema
# applied without the layer above it.
#
# Nothing is lost by leaving it out. SQLite resolves a foreign key's parent table by name when a row
# is written rather than when the table is created, so the cascade holds whichever order the two
# were made in; there is a test that deletes a user and checks its ratings go with it.
register_schema_initializer(
    USER_STATE_COMPONENT,
    USER_STATE_VERSION,
    initialize_user_state,
    depends_on=[CONTENT_COMPONENT],
    baseline=6,
)
